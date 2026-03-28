from collections import defaultdict
from datetime import datetime, timedelta
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import Campaign, CampaignStatus, FacebookPage, Video, VideoStatus
from app.services.ai_generator import generate_caption
from app.services.observability import record_event
from app.services.task_queue import (
    TASK_TYPE_CAMPAIGN_SYNC,
    TASK_TYPE_VIDEO_RETRY,
    enqueue_task,
)

router = APIRouter(prefix="/campaigns", tags=["Chiến dịch"])


class CampaignCreate(BaseModel):
    name: str
    source_url: str
    auto_post: bool = False
    target_page_id: str | None = None
    schedule_interval: int = Field(default=0, ge=0)


class VideoCaptionUpdate(BaseModel):
    ai_caption: str = Field(min_length=3, max_length=5000)


def serialize_datetime(value):
    return value.isoformat() if value else None


def normalize_status(value):
    return value.value if hasattr(value, "value") else value


def set_campaign_sync_state(campaign: Campaign, status: str, error: str | None = None, finished_at: datetime | None = None):
    campaign.last_sync_status = status
    campaign.last_sync_error = error[:1000] if error else None
    if finished_at:
        campaign.last_synced_at = finished_at


def parse_uuid_or_400(raw_id: str, label: str):
    try:
        return uuid.UUID(raw_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{label} không hợp lệ.") from exc


def build_campaign_summary_map(db: Session):
    summary_map = defaultdict(
        lambda: {"total": 0, "pending": 0, "downloading": 0, "ready": 0, "posted": 0, "failed": 0}
    )
    rows = (
        db.query(Video.campaign_id, Video.status, func.count(Video.id))
        .group_by(Video.campaign_id, Video.status)
        .all()
    )
    for campaign_id, status, count in rows:
        key = normalize_status(status)
        summary_map[campaign_id]["total"] += count
        summary_map[campaign_id][key] = count
    return summary_map


def build_page_name_map(db: Session):
    return {page.page_id: page.page_name for page in db.query(FacebookPage).all()}


def serialize_campaign(campaign: Campaign, summary_map, page_name_map):
    video_counts = summary_map.get(
        campaign.id,
        {"total": 0, "pending": 0, "downloading": 0, "ready": 0, "posted": 0, "failed": 0},
    )
    return {
        "id": str(campaign.id),
        "name": campaign.name,
        "source_url": campaign.source_url,
        "status": normalize_status(campaign.status),
        "auto_post": campaign.auto_post,
        "target_page_id": campaign.target_page_id,
        "target_page_name": page_name_map.get(campaign.target_page_id),
        "schedule_interval": campaign.schedule_interval,
        "last_synced_at": serialize_datetime(campaign.last_synced_at),
        "last_sync_status": campaign.last_sync_status or "idle",
        "last_sync_error": campaign.last_sync_error,
        "created_at": serialize_datetime(campaign.created_at),
        "updated_at": serialize_datetime(campaign.updated_at),
        "video_counts": video_counts,
    }


def serialize_video(video: Video, page_name_map=None):
    campaign = video.campaign
    page_name = None
    campaign_name = None
    target_page_id = None
    campaign_status = None
    if campaign:
        campaign_name = campaign.name
        target_page_id = campaign.target_page_id
        campaign_status = normalize_status(campaign.status)
        if page_name_map and target_page_id:
            page_name = page_name_map.get(target_page_id)

    return {
        "id": str(video.id),
        "campaign_id": str(video.campaign_id),
        "campaign_name": campaign_name,
        "campaign_status": campaign_status,
        "target_page_id": target_page_id,
        "target_page_name": page_name,
        "original_id": video.original_id,
        "source_video_url": video.source_video_url,
        "file_path": video.file_path,
        "original_caption": video.original_caption,
        "ai_caption": video.ai_caption,
        "status": normalize_status(video.status),
        "publish_time": serialize_datetime(video.publish_time),
        "fb_post_id": video.fb_post_id,
        "last_error": video.last_error,
        "retry_count": video.retry_count or 0,
        "created_at": serialize_datetime(video.created_at),
        "updated_at": serialize_datetime(video.updated_at),
    }


def get_campaign_or_404(db: Session, campaign_id: str):
    campaign_uuid = parse_uuid_or_400(campaign_id, "Mã chiến dịch")
    campaign = db.query(Campaign).filter(Campaign.id == campaign_uuid).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Không tìm thấy chiến dịch.")
    return campaign


def get_video_or_404(db: Session, video_id: str):
    video_uuid = parse_uuid_or_400(video_id, "Mã video")
    video = db.query(Video).filter(Video.id == video_uuid).first()
    if not video:
        raise HTTPException(status_code=404, detail="Không tìm thấy video.")
    return video


def safe_remove_file(path: str | None):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


@router.post("/")
def create_campaign(campaign_in: CampaignCreate, db: Session = Depends(get_db)):
    if campaign_in.target_page_id:
        page = db.query(FacebookPage).filter(FacebookPage.page_id == campaign_in.target_page_id).first()
        if not page:
            raise HTTPException(status_code=400, detail="Trang đích chưa được cấu hình trong hệ thống.")

    db_campaign = Campaign(
        name=campaign_in.name.strip(),
        source_url=campaign_in.source_url.strip(),
        auto_post=campaign_in.auto_post,
        target_page_id=campaign_in.target_page_id,
        schedule_interval=campaign_in.schedule_interval,
        status=CampaignStatus.active,
        last_sync_status="queued",
    )
    db.add(db_campaign)
    db.commit()
    db.refresh(db_campaign)

    task = enqueue_task(
        db,
        task_type=TASK_TYPE_CAMPAIGN_SYNC,
        entity_type="campaign",
        entity_id=str(db_campaign.id),
        payload={"campaign_id": str(db_campaign.id), "source_url": db_campaign.source_url, "allow_paused": False},
        priority=20,
        max_attempts=2,
    )
    record_event(
        "campaign",
        "info",
        "Đã tạo chiến dịch mới và đưa vào hàng đợi đồng bộ.",
        db=db,
        details={"campaign_id": str(db_campaign.id), "task_id": str(task.id)},
    )
    return {
        "message": "Đã tạo chiến dịch và xếp vào hàng đợi đồng bộ.",
        "campaign_id": str(db_campaign.id),
        "task_id": str(task.id),
    }


@router.get("/")
def get_campaigns(db: Session = Depends(get_db)):
    campaigns = db.query(Campaign).order_by(Campaign.created_at.desc()).all()
    page_name_map = build_page_name_map(db)
    summary_map = build_campaign_summary_map(db)
    return [serialize_campaign(campaign, summary_map, page_name_map) for campaign in campaigns]


@router.post("/{campaign_id}/sync")
def sync_campaign(campaign_id: str, db: Session = Depends(get_db)):
    campaign = get_campaign_or_404(db, campaign_id)
    if campaign.last_sync_status == "syncing":
        raise HTTPException(status_code=400, detail="Chiến dịch này đang đồng bộ, chưa thể chạy lại.")

    set_campaign_sync_state(campaign, "queued")
    db.commit()
    db.refresh(campaign)

    task = enqueue_task(
        db,
        task_type=TASK_TYPE_CAMPAIGN_SYNC,
        entity_type="campaign",
        entity_id=str(campaign.id),
        payload={"campaign_id": str(campaign.id), "source_url": campaign.source_url, "allow_paused": True},
        priority=25,
        max_attempts=2,
    )

    page_name_map = build_page_name_map(db)
    summary_map = build_campaign_summary_map(db)
    return {
        "message": f"Đã xếp lịch đồng bộ lại cho chiến dịch '{campaign.name}'.",
        "campaign": serialize_campaign(campaign, summary_map, page_name_map),
        "task_id": str(task.id),
    }


@router.post("/{campaign_id}/pause")
def pause_campaign(campaign_id: str, db: Session = Depends(get_db)):
    campaign = get_campaign_or_404(db, campaign_id)
    campaign.status = CampaignStatus.paused
    db.commit()
    record_event(
        "campaign",
        "warning",
        "Đã tạm dừng chiến dịch.",
        db=db,
        details={"campaign_id": str(campaign.id), "campaign_name": campaign.name},
    )
    return {"message": f"Đã tạm dừng chiến dịch '{campaign.name}'."}


@router.post("/{campaign_id}/resume")
def resume_campaign(campaign_id: str, db: Session = Depends(get_db)):
    campaign = get_campaign_or_404(db, campaign_id)
    campaign.status = CampaignStatus.active
    db.commit()
    record_event(
        "campaign",
        "info",
        "Đã kích hoạt lại chiến dịch.",
        db=db,
        details={"campaign_id": str(campaign.id), "campaign_name": campaign.name},
    )
    return {"message": f"Đã kích hoạt lại chiến dịch '{campaign.name}'."}


@router.delete("/{campaign_id}")
def delete_campaign(campaign_id: str, db: Session = Depends(get_db)):
    campaign = get_campaign_or_404(db, campaign_id)
    file_paths = [row[0] for row in db.query(Video.file_path).filter(Video.campaign_id == campaign.id).all() if row[0]]
    deleted_videos = db.query(Video).filter(Video.campaign_id == campaign.id).delete(synchronize_session=False)
    campaign_name = campaign.name
    db.delete(campaign)
    db.commit()

    for file_path in file_paths:
        safe_remove_file(file_path)

    record_event(
        "campaign",
        "warning",
        "Đã xóa chiến dịch và video liên quan.",
        db=db,
        details={"campaign_name": campaign_name, "deleted_videos": deleted_videos},
    )
    return {"message": f"Đã xóa chiến dịch '{campaign_name}' và {deleted_videos} video liên quan."}


@router.get("/stats")
def get_video_stats(db: Session = Depends(get_db)):
    total = db.query(Video).count()
    pending = db.query(Video).filter(Video.status == VideoStatus.pending).count()
    downloading = db.query(Video).filter(Video.status == VideoStatus.downloading).count()
    ready = db.query(Video).filter(Video.status == VideoStatus.ready).count()
    posted = db.query(Video).filter(Video.status == VideoStatus.posted).count()
    failed = db.query(Video).filter(Video.status == VideoStatus.failed).count()
    next_publish = db.query(func.min(Video.publish_time)).filter(Video.status == VideoStatus.ready).scalar()
    queue_end = db.query(func.max(Video.publish_time)).filter(Video.status == VideoStatus.ready).scalar()
    last_posted = db.query(func.max(Video.updated_at)).filter(Video.status == VideoStatus.posted).scalar()

    return {
        "total": total,
        "pending": pending + downloading,
        "processing": pending + downloading,
        "downloading": downloading,
        "ready": ready,
        "posted": posted,
        "failed": failed,
        "active_campaigns": db.query(Campaign).filter(Campaign.status == CampaignStatus.active).count(),
        "paused_campaigns": db.query(Campaign).filter(Campaign.status == CampaignStatus.paused).count(),
        "connected_pages": db.query(FacebookPage).count(),
        "next_publish": serialize_datetime(next_publish),
        "queue_end": serialize_datetime(queue_end),
        "last_posted": serialize_datetime(last_posted),
    }


@router.get("/videos")
def get_videos(
    page: int = 1,
    limit: int = 10,
    status: str | None = None,
    campaign_id: str | None = None,
    db: Session = Depends(get_db),
):
    if page < 1 or limit < 1:
        raise HTTPException(status_code=400, detail="Phân trang không hợp lệ.")

    query = db.query(Video)

    if status and status != "all":
        allowed_statuses = {video_status.value for video_status in VideoStatus}
        if status not in allowed_statuses:
            raise HTTPException(status_code=400, detail="Bộ lọc trạng thái không hợp lệ.")
        query = query.filter(Video.status == status)

    if campaign_id and campaign_id != "all":
        query = query.filter(Video.campaign_id == parse_uuid_or_400(campaign_id, "Mã chiến dịch"))

    total = query.count()
    offset = (page - 1) * limit
    videos = (
        query.order_by(Video.publish_time.desc(), Video.updated_at.desc(), Video.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    page_name_map = build_page_name_map(db)

    return {
        "videos": [serialize_video(video, page_name_map) for video in videos],
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    }


@router.post("/videos/{video_id}/priority")
def prioritize_video(video_id: str, db: Session = Depends(get_db)):
    video = get_video_or_404(db, video_id)
    if normalize_status(video.status) != VideoStatus.ready.value:
        raise HTTPException(status_code=400, detail="Chỉ có thể ưu tiên video đang ở trạng thái sẵn sàng.")
    if not video.campaign or not video.campaign.target_page_id:
        raise HTTPException(status_code=400, detail="Video này chưa gắn với trang đích.")

    earliest_other_publish = (
        db.query(func.min(Video.publish_time))
        .join(Campaign)
        .filter(
            Campaign.target_page_id == video.campaign.target_page_id,
            Campaign.status == CampaignStatus.active,
            Video.status == VideoStatus.ready,
            Video.id != video.id,
        )
        .scalar()
    )

    now = datetime.utcnow()
    video.publish_time = min(now, earliest_other_publish - timedelta(seconds=1)) if earliest_other_publish else now
    video.last_error = None
    db.commit()
    db.refresh(video)
    page_name_map = build_page_name_map(db)
    return {
        "message": f"Đã đẩy video {video.original_id} lên đầu hàng chờ.",
        "video": serialize_video(video, page_name_map),
    }


@router.patch("/videos/{video_id}/caption")
def update_video_caption(video_id: str, payload: VideoCaptionUpdate, db: Session = Depends(get_db)):
    video = get_video_or_404(db, video_id)
    video.ai_caption = payload.ai_caption.strip()
    video.last_error = None
    db.commit()
    db.refresh(video)
    page_name_map = build_page_name_map(db)
    return {
        "message": f"Đã cập nhật chú thích cho video {video.original_id}.",
        "video": serialize_video(video, page_name_map),
    }


@router.post("/videos/{video_id}/generate-caption")
def regenerate_video_caption(video_id: str, db: Session = Depends(get_db)):
    video = get_video_or_404(db, video_id)
    if not video.original_caption:
        raise HTTPException(status_code=400, detail="Video này không có chú thích gốc để AI viết lại.")

    try:
        video.ai_caption = generate_caption(video.original_caption)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Không thể tạo chú thích AI lúc này: {exc}") from exc
    video.last_error = None
    db.commit()
    db.refresh(video)
    page_name_map = build_page_name_map(db)
    return {
        "message": f"Đã tạo lại chú thích AI cho video {video.original_id}.",
        "video": serialize_video(video, page_name_map),
    }


@router.post("/videos/{video_id}/retry")
def retry_video(video_id: str, db: Session = Depends(get_db)):
    video = get_video_or_404(db, video_id)

    if normalize_status(video.status) == VideoStatus.posted.value:
        raise HTTPException(status_code=400, detail="Video đã đăng thành công, không cần thử lại.")

    if video.file_path and os.path.exists(video.file_path):
        video.status = VideoStatus.ready
        video.publish_time = datetime.utcnow()
        video.last_error = None
        video.fb_post_id = None
        db.commit()
        db.refresh(video)
        page_name_map = build_page_name_map(db)
        return {
            "message": f"Đã đưa video {video.original_id} trở lại hàng chờ đăng.",
            "video": serialize_video(video, page_name_map),
        }

    if not video.source_video_url:
        raise HTTPException(status_code=400, detail="Video này không có liên kết nguồn để tải lại.")

    video.status = VideoStatus.downloading
    video.last_error = None
    video.fb_post_id = None
    db.commit()

    task = enqueue_task(
        db,
        task_type=TASK_TYPE_VIDEO_RETRY,
        entity_type="video",
        entity_id=str(video.id),
        payload={"video_id": str(video.id)},
        priority=15,
        max_attempts=3,
    )
    return {"message": f"Đã xếp lịch thử tải lại video {video.original_id}.", "task_id": str(task.id)}

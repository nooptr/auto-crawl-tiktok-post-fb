import json
from json import JSONDecodeError

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.api.auth import require_authenticated_user
from app.core.database import get_db
from app.models.models import FacebookPage, InteractionLog, InteractionStatus, User
from app.services.observability import record_event
from app.services.runtime_settings import resolve_runtime_value
from app.services.security import verify_facebook_signature
from app.services.task_queue import TASK_TYPE_COMMENT_REPLY, enqueue_task

router = APIRouter(prefix="/webhooks", tags=["Webhook"])


def serialize_interaction_log(log: InteractionLog) -> dict:
    return {
        "id": str(log.id),
        "page_id": log.page_id,
        "post_id": log.post_id,
        "comment_id": log.comment_id,
        "user_id": log.user_id,
        "user_message": log.user_message,
        "ai_reply": log.ai_reply,
        "status": log.status.value if hasattr(log.status, "value") else log.status,
        "created_at": log.created_at.isoformat() if log.created_at else None,
        "updated_at": log.updated_at.isoformat() if log.updated_at else None,
    }


@router.get("/fb")
def verify_webhook(request: Request, db: Session = Depends(get_db)):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    verify_token = resolve_runtime_value("FB_VERIFY_TOKEN", db=db)

    if mode == "subscribe" and token == verify_token:
        return PlainTextResponse(content=challenge)
    raise HTTPException(status_code=403, detail="Mã xác minh webhook không hợp lệ")


@router.post("/fb")
async def handle_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    app_secret = resolve_runtime_value("FB_APP_SECRET", db=db)
    if app_secret and not verify_facebook_signature(body, signature, app_secret=app_secret):
        raise HTTPException(status_code=403, detail="Chữ ký webhook không hợp lệ")

    try:
        payload = json.loads(body.decode("utf-8"))
    except JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Dữ liệu webhook không phải JSON hợp lệ.") from exc

    if payload.get("object") == "page":
        for entry in payload.get("entry", []):
            page_id = entry.get("id")
            for change in entry.get("changes", []):
                value = change.get("value", {})

                if change.get("field") == "feed" and value.get("item") == "status" and value.get("message") == "Example post content.":
                    record_event(
                        "webhook",
                        "info",
                        "Đã nhận sự kiện thử webhook từ Facebook.",
                        db=db,
                        details={"page_id": page_id},
                    )
                    continue

                if change.get("field") == "feed" and value.get("item") == "comment" and value.get("verb") == "add":
                    comment_id = value.get("comment_id")
                    message = value.get("message")
                    post_id = value.get("post_id")
                    sender_id = value.get("from", {}).get("id")

                    if sender_id == page_id:
                        continue

                    existing = db.query(InteractionLog).filter(InteractionLog.comment_id == comment_id).first()
                    if existing:
                        continue

                    page_config = db.query(FacebookPage).filter(FacebookPage.page_id == page_id).first()
                    if not page_config:
                        record_event(
                            "webhook",
                            "warning",
                            "Nhận bình luận từ trang chưa cấu hình.",
                            db=db,
                            details={"page_id": page_id, "comment_id": comment_id},
                        )
                        continue

                    log = InteractionLog(
                        page_id=page_id,
                        post_id=post_id,
                        comment_id=comment_id,
                        user_id=sender_id,
                        user_message=message,
                        status=InteractionStatus.pending,
                    )
                    db.add(log)
                    db.commit()
                    db.refresh(log)

                    task = enqueue_task(
                        db,
                        task_type=TASK_TYPE_COMMENT_REPLY,
                        entity_type="interaction_log",
                        entity_id=str(log.id),
                        payload={"interaction_log_id": str(log.id)},
                        priority=10,
                        max_attempts=3,
                    )
                    record_event(
                        "webhook",
                        "info",
                        "Đã ghi nhận bình luận mới và đưa vào hàng đợi phản hồi.",
                        db=db,
                        details={"comment_id": comment_id, "page_id": page_id, "task_id": str(task.id)},
                    )

    return {"status": "đã nhận"}


@router.get("/logs")
def get_interaction_logs(
    db: Session = Depends(get_db),
    _: User = Depends(require_authenticated_user),
):
    logs = db.query(InteractionLog).order_by(InteractionLog.created_at.desc()).limit(50).all()
    return [serialize_interaction_log(log) for log in logs]

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.core.database import get_db
from app.models.models import FacebookPage
from app.services.observability import record_event
from app.services.security import decrypt_secret, encrypt_secret, is_secret_encrypted, mask_secret
from app.services.fb_graph import inspect_page_access

router = APIRouter(prefix="/facebook", tags=["Trang Facebook"])

class FacebookPageCreate(BaseModel):
    page_id: str
    page_name: str
    long_lived_access_token: str

def get_token_kind(token: str | None) -> str:
    if not token:
        return "missing"
    try:
        plain_token = decrypt_secret(token)
    except ValueError:
        return "invalid_encryption"
    if plain_token.startswith("http://") or plain_token.startswith("https://"):
        return "legacy_webhook"
    return "page_access_token"

@router.post("/config")
def set_facebook_config(page_in: FacebookPageCreate, db: Session = Depends(get_db)):
    normalized_token = page_in.long_lived_access_token.strip()

    if get_token_kind(normalized_token) == "legacy_webhook":
        raise HTTPException(
            status_code=400,
            detail="Hãy nhập mã truy cập trang Facebook thật. Liên kết webhook cũ không còn dùng để đăng bài hoặc trả lời bình luận."
        )

    page = db.query(FacebookPage).filter(FacebookPage.page_id == page_in.page_id).first()
    if page:
        page.page_name = page_in.page_name
        page.long_lived_access_token = encrypt_secret(normalized_token)
    else:
        page = FacebookPage(
            page_id=page_in.page_id,
            page_name=page_in.page_name,
            long_lived_access_token=encrypt_secret(normalized_token)
        )
        db.add(page)
    db.commit()
    record_event(
        "facebook",
        "info",
        "Đã lưu cấu hình trang Facebook.",
        db=db,
        details={"page_id": page_in.page_id, "page_name": page_in.page_name},
    )
    return {"message": "Đã lưu mã truy cập Facebook thành công!"}

@router.get("/config")
def get_facebook_config(db: Session = Depends(get_db)):
    pages = db.query(FacebookPage).all()
    should_commit = False
    normalized_pages = []

    for page in pages:
        raw_token = page.long_lived_access_token
        if raw_token and not is_secret_encrypted(raw_token):
            page.long_lived_access_token = encrypt_secret(raw_token)
            raw_token = page.long_lived_access_token
            should_commit = True

        try:
            decrypted = decrypt_secret(raw_token)
            token_kind = get_token_kind(raw_token)
            token_preview = mask_secret(decrypted)
        except ValueError:
            token_kind = "invalid_encryption"
            token_preview = None

        normalized_pages.append(
            {
                "page_id": page.page_id,
                "page_name": page.page_name,
                "has_token": bool(raw_token),
                "token_kind": token_kind,
                "token_preview": token_preview,
                "token_is_encrypted": bool(raw_token and is_secret_encrypted(raw_token)),
            }
        )

    if should_commit:
        db.commit()

    return normalized_pages


@router.get("/config/{page_id}/validate")
def validate_facebook_page(page_id: str, db: Session = Depends(get_db)):
    page = db.query(FacebookPage).filter(FacebookPage.page_id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Không tìm thấy trang Facebook trong hệ thống.")

    if not page.long_lived_access_token:
        raise HTTPException(status_code=400, detail="Trang Facebook này chưa có mã truy cập để kiểm tra.")

    token_kind = get_token_kind(page.long_lived_access_token)
    if token_kind != "page_access_token":
        raise HTTPException(status_code=400, detail="Mã truy cập hiện tại không phải mã truy cập trang Facebook hợp lệ.")

    try:
        access_token = decrypt_secret(page.long_lived_access_token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = inspect_page_access(page.page_id, access_token)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "Không thể xác minh trang Facebook."))
    record_event(
        "facebook",
        "info",
        "Đã xác minh mã truy cập trang Facebook.",
        db=db,
        details={"page_id": page.page_id, "page_name": page.page_name},
    )
    return result

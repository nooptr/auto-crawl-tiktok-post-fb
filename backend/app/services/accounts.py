from __future__ import annotations

import secrets

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import User, UserRole
from app.services.observability import record_event
from app.services.security import hash_password, validate_password_strength


def serialize_user(user: User) -> dict:
    return {
        "id": str(user.id),
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role.value if hasattr(user.role, "value") else user.role,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }


def ensure_default_admin(db: Session) -> User:
    existing = db.query(User).filter(User.username == settings.DEFAULT_ADMIN_USERNAME).first()
    if existing:
        return existing

    default_admin = User(
        username=settings.DEFAULT_ADMIN_USERNAME,
        display_name=settings.DEFAULT_ADMIN_DISPLAY_NAME,
        password_hash=hash_password(settings.ADMIN_PASSWORD),
        role=UserRole.admin,
        is_active=True,
        must_change_password=True,
    )
    db.add(default_admin)
    db.commit()
    db.refresh(default_admin)
    record_event(
        "auth",
        "warning",
        "Đã tạo tài khoản quản trị mặc định.",
        db=db,
        details={"username": default_admin.username},
    )
    return default_admin


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    role: str,
    display_name: str | None = None,
    must_change_password: bool = False,
) -> User:
    normalized_username = username.strip().lower()
    if db.query(User).filter(User.username == normalized_username).first():
        raise ValueError("Tên đăng nhập đã tồn tại.")

    password_error = validate_password_strength(password)
    if password_error:
        raise ValueError(password_error)

    user = User(
        username=normalized_username,
        display_name=(display_name or "").strip() or None,
        password_hash=hash_password(password),
        role=UserRole(role),
        is_active=True,
        must_change_password=must_change_password,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def count_admin_users(db: Session, *, active_only: bool = False, exclude_user_id: str | None = None) -> int:
    query = db.query(User).filter(User.role == UserRole.admin)
    if active_only:
        query = query.filter(User.is_active.is_(True))
    if exclude_user_id:
        query = query.filter(User.id != exclude_user_id)
    return query.count()


def generate_temporary_password(length: int = 12) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))

import base64
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from threading import Lock
import time
from uuid import UUID

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

ENCRYPTED_PREFIX = "enc::"
_LOGIN_ATTEMPTS = defaultdict(list)
_LOGIN_LOCKS = {}
_RATE_LIMIT_LOCK = Lock()


def _build_fernet_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def get_fernet() -> Fernet:
    return Fernet(_build_fernet_key(settings.TOKEN_ENCRYPTION_SECRET))


def is_secret_encrypted(value: str | None) -> bool:
    return bool(value and value.startswith(ENCRYPTED_PREFIX))


def encrypt_secret(value: str | None) -> str | None:
    if not value:
        return value
    if is_secret_encrypted(value):
        return value
    encrypted = get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{ENCRYPTED_PREFIX}{encrypted}"


def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return value
    if not is_secret_encrypted(value):
        return value

    encrypted = value[len(ENCRYPTED_PREFIX) :].encode("utf-8")
    try:
        return get_fernet().decrypt(encrypted).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Không thể giải mã secret. Hãy kiểm tra TOKEN_ENCRYPTION_SECRET.") from exc


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    suffix = value[-4:] if len(value) >= 4 else value
    return f"••••••••{suffix}"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def validate_password_strength(password: str) -> str | None:
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        return f"Mật khẩu phải có ít nhất {settings.PASSWORD_MIN_LENGTH} ký tự."
    if password.isdigit() or password.isalpha():
        return "Mật khẩu nên gồm cả chữ và số để an toàn hơn."
    return None


def create_access_token(user_id: UUID | str, username: str, role: str) -> tuple[str, int]:
    issued_at = datetime.now(timezone.utc)
    expires_delta = timedelta(minutes=settings.AUTH_TOKEN_EXPIRE_MINUTES)
    expires_at = issued_at + expires_delta
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": issued_at,
        "exp": expires_at,
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])


def get_client_identity(request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _rate_limit_key(client_id: str, username: str | None = None) -> str:
    normalized_username = (username or "").strip().lower()
    return f"{client_id}:{normalized_username}" if normalized_username else client_id


def check_login_rate_limit(client_id: str, username: str | None = None) -> int:
    key = _rate_limit_key(client_id, username)
    now = time.time()
    with _RATE_LIMIT_LOCK:
        locked_until = _LOGIN_LOCKS.get(key)
        if locked_until and locked_until > now:
            return int(locked_until - now)

        attempts = [
            attempt
            for attempt in _LOGIN_ATTEMPTS.get(key, [])
            if now - attempt < settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS
        ]
        _LOGIN_ATTEMPTS[key] = attempts

        if len(attempts) >= settings.LOGIN_RATE_LIMIT_ATTEMPTS:
            locked_until = now + settings.LOGIN_LOCKOUT_SECONDS
            _LOGIN_LOCKS[key] = locked_until
            return int(settings.LOGIN_LOCKOUT_SECONDS)

    return 0


def register_failed_login(client_id: str, username: str | None = None) -> int:
    key = _rate_limit_key(client_id, username)
    now = time.time()
    with _RATE_LIMIT_LOCK:
        attempts = [
            attempt
            for attempt in _LOGIN_ATTEMPTS.get(key, [])
            if now - attempt < settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS
        ]
        attempts.append(now)
        _LOGIN_ATTEMPTS[key] = attempts

        if len(attempts) >= settings.LOGIN_RATE_LIMIT_ATTEMPTS:
            _LOGIN_LOCKS[key] = now + settings.LOGIN_LOCKOUT_SECONDS
            return settings.LOGIN_LOCKOUT_SECONDS
    return 0


def clear_login_rate_limit(client_id: str, username: str | None = None) -> None:
    key = _rate_limit_key(client_id, username)
    with _RATE_LIMIT_LOCK:
        _LOGIN_ATTEMPTS.pop(key, None)
        _LOGIN_LOCKS.pop(key, None)


def verify_facebook_signature(body: bytes, signature_header: str | None, app_secret: str | None = None) -> bool:
    secret = app_secret if app_secret is not None else settings.FB_APP_SECRET
    if not secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)


def is_default_secret(value: str, default_value: str) -> bool:
    return value == default_value

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.models import RuntimeSetting
from app.services.security import decrypt_secret, encrypt_secret, mask_secret

RUNTIME_ENV_FILE = Path(__file__).resolve().parents[2] / "runtime.env"

RUNTIME_SETTING_SPECS = {
    "BASE_URL": {
        "label": "BASE_URL",
        "description": "URL công khai của hệ thống",
        "is_secret": False,
        "requires_restart": False,
    },
    "FB_VERIFY_TOKEN": {
        "label": "FB_VERIFY_TOKEN",
        "description": "Mã xác minh webhook Facebook",
        "is_secret": False,
        "requires_restart": False,
    },
    "FB_APP_SECRET": {
        "label": "FB_APP_SECRET",
        "description": "App secret để xác minh chữ ký webhook",
        "is_secret": True,
        "requires_restart": False,
    },
    "GEMINI_API_KEY": {
        "label": "GEMINI_API_KEY",
        "description": "Khóa Gemini để sinh caption và trả lời",
        "is_secret": True,
        "requires_restart": False,
    },
    "TUNNEL_TOKEN": {
        "label": "TUNNEL_TOKEN",
        "description": "Token Cloudflare Tunnel",
        "is_secret": True,
        "requires_restart": True,
    },
}


def get_runtime_setting_specs() -> dict[str, dict]:
    return RUNTIME_SETTING_SPECS


def get_runtime_default_value(key: str) -> str:
    return str(getattr(settings, key, "") or "")


def _normalize_value(value) -> str | None:
    if value is None:
        return None
    return str(value).strip()


def _decode_record_value(record: RuntimeSetting | None) -> str | None:
    if not record or record.value is None:
        return None
    if record.is_secret:
        return decrypt_secret(record.value)
    return record.value


def resolve_runtime_value(key: str, db: Session | None = None) -> str:
    if key not in RUNTIME_SETTING_SPECS:
        return get_runtime_default_value(key)

    if db is not None:
        record = db.get(RuntimeSetting, key)
        decoded = _decode_record_value(record)
        if decoded is not None:
            return decoded
        return get_runtime_default_value(key)

    temp_db = None
    try:
        temp_db = SessionLocal()
        return resolve_runtime_value(key, db=temp_db)
    except Exception:
        return get_runtime_default_value(key)
    finally:
        if temp_db is not None:
            temp_db.close()


def build_runtime_settings_payload(db: Session) -> dict:
    resolved_values = {key: resolve_runtime_value(key, db=db) for key in RUNTIME_SETTING_SPECS}
    settings_payload = {}

    for key, spec in RUNTIME_SETTING_SPECS.items():
        record = db.get(RuntimeSetting, key)
        value = resolved_values[key]
        settings_payload[key] = {
            "key": key,
            "label": spec["label"],
            "description": spec["description"],
            "is_secret": spec["is_secret"],
            "requires_restart": spec["requires_restart"],
            "value": value,
            "display_value": mask_secret(value) if spec["is_secret"] else value,
            "source": "override" if record else "env",
            "is_configured": bool(value),
            "updated_at": record.updated_at.isoformat() if record and record.updated_at else None,
            "updated_by_user_id": record.updated_by_user_id if record else None,
        }

    base_url = resolved_values["BASE_URL"].rstrip("/")
    webhook_url = f"{base_url}/webhooks/fb" if base_url else ""

    return {
        "settings": settings_payload,
        "derived": {
            "base_url": base_url,
            "webhook_url": webhook_url,
            "verify_token": resolved_values["FB_VERIFY_TOKEN"],
            "runtime_env_file": str(RUNTIME_ENV_FILE),
        },
    }


def write_runtime_env_file(db: Session) -> None:
    resolved_values = {key: resolve_runtime_value(key, db=db) for key in RUNTIME_SETTING_SPECS}
    lines = [
        "# Tự sinh từ dashboard quản trị",
        "# Khởi động lại service liên quan sau khi thay đổi nếu cần",
    ]
    for key in RUNTIME_SETTING_SPECS:
        value = resolved_values[key].replace("\n", "\\n")
        lines.append(f"{key}={value}")
    RUNTIME_ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_runtime_settings(db: Session, updates: dict[str, str | None], *, actor_user_id: str | None = None) -> list[str]:
    changed_keys: list[str] = []

    for key, raw_value in updates.items():
        if key not in RUNTIME_SETTING_SPECS or raw_value is None:
            continue

        normalized = _normalize_value(raw_value)
        record = db.get(RuntimeSetting, key)
        spec = RUNTIME_SETTING_SPECS[key]
        current_value = resolve_runtime_value(key, db=db)

        if normalized == "":
            if record is not None:
                db.delete(record)
                changed_keys.append(key)
            continue

        if normalized == current_value and record is not None:
            continue

        stored_value = encrypt_secret(normalized) if spec["is_secret"] else normalized
        if record is None:
            record = RuntimeSetting(
                key=key,
                is_secret=spec["is_secret"],
                updated_by_user_id=actor_user_id,
            )
            db.add(record)

        record.value = stored_value
        record.is_secret = spec["is_secret"]
        record.updated_by_user_id = actor_user_id
        changed_keys.append(key)

    db.commit()
    write_runtime_env_file(db)
    return changed_keys

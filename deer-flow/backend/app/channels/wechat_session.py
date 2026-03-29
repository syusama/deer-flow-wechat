"""Shared session helpers for the DeerFlow WeChat channel and login CLI."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_WECHAT_BASE_URL = "https://ilinkai.weixin.qq.com"


def build_session_payload(
    *,
    bot_token: str,
    base_url: str,
    ilink_bot_id: str = "",
    ilink_user_id: str = "",
    updated_at: float | None = None,
) -> dict[str, Any]:
    return {
        "bot_token": bot_token,
        "base_url": base_url,
        "ilink_bot_id": ilink_bot_id,
        "ilink_user_id": ilink_user_id,
        "updated_at": float(updated_at if updated_at is not None else time.time()),
    }


def build_session_payload_from_status(
    status: Any,
    *,
    fallback_base_url: str = DEFAULT_WECHAT_BASE_URL,
    updated_at: float | None = None,
) -> dict[str, Any]:
    return build_session_payload(
        bot_token=str(getattr(status, "bot_token", "") or ""),
        base_url=str(getattr(status, "baseurl", "") or fallback_base_url),
        ilink_bot_id=str(getattr(status, "ilink_bot_id", "") or ""),
        ilink_user_id=str(getattr(status, "ilink_user_id", "") or ""),
        updated_at=updated_at,
    )


def load_session_data(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}

    session_path = Path(path)
    if not session_path.exists():
        return {}

    try:
        payload = json.loads(session_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read WeChat session file: %s", session_path)
        return {}

    return payload if isinstance(payload, dict) else {}


def write_session_data(path: str | Path, payload: dict[str, Any]) -> Path:
    session_path = Path(path)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return session_path

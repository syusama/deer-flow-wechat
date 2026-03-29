"""WeChat channel backed by wechat-link long polling."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.channels.base import Channel
from app.channels.message_bus import InboundMessage, InboundMessageType, MessageBus, OutboundMessage, ResolvedAttachment
from app.channels.wechat_session import DEFAULT_WECHAT_BASE_URL, load_session_data

logger = logging.getLogger(__name__)

DEFAULT_WECHAT_CHANNEL_VERSION = "0.1.0"
DEFAULT_REPLY_CONTEXT_TTL_SECONDS = 1800.0
DEFAULT_POLL_INTERVAL_SECONDS = 1.0


@dataclass(slots=True)
class ReplyContext:
    to_user_id: str
    context_token: str
    typing_ticket: str | None = None
    created_at: float = field(default_factory=time.time)


class WeChatChannel(Channel):
    """WeChat IM channel using the unofficial wechat-link SDK."""

    def __init__(self, bus: MessageBus, config: dict[str, Any]) -> None:
        super().__init__(name="wechat", bus=bus, config=config)
        self._client = None
        self._cursor_store = None
        self._thread: threading.Thread | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._reply_context: dict[str, ReplyContext] = {}
        self._reply_context_ttl_seconds = float(config.get("reply_context_ttl_seconds", DEFAULT_REPLY_CONTEXT_TTL_SECONDS))
        self._poll_interval_seconds = float(config.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS))

    async def start(self) -> None:
        if self._running:
            return

        client_kwargs = self._resolve_client_kwargs()
        if not client_kwargs["bot_token"]:
            logger.error("WeChat channel requires bot_token or a valid session_file")
            return

        try:
            self._client = self._create_client(**client_kwargs)
            self._cursor_store = self._create_cursor_store(self.config.get("cursor_file"))
        except ImportError:
            logger.error("wechat-link is not installed. Install it with: pip install wechat-link")
            return
        except Exception:
            logger.exception("Failed to initialize WeChat channel")
            return

        self._main_loop = asyncio.get_event_loop()
        self._running = True
        self.bus.subscribe_outbound(self._on_outbound)
        self._thread = self._create_polling_thread()
        self._thread.start()
        logger.info("WeChat channel started")

    async def stop(self) -> None:
        self._running = False
        self.bus.unsubscribe_outbound(self._on_outbound)

        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

        client = self._client
        self._client = None
        self._cursor_store = None

        if client and hasattr(client, "close"):
            await asyncio.to_thread(client.close)

        logger.info("WeChat channel stopped")

    async def send(self, msg: OutboundMessage, *, _max_retries: int = 3) -> None:
        client = self._require_client()
        reply_context = self._require_reply_context(msg.thread_ts)

        last_exc: Exception | None = None
        for attempt in range(_max_retries):
            try:
                await asyncio.to_thread(
                    client.send_text,
                    to_user_id=reply_context.to_user_id,
                    text=msg.text,
                    context_token=reply_context.context_token,
                )
                return
            except Exception as exc:
                last_exc = exc
                if attempt < _max_retries - 1:
                    await asyncio.sleep(2**attempt)

        raise RuntimeError(f"WeChat text send failed: {last_exc}") from last_exc

    async def send_file(self, msg: OutboundMessage, attachment: ResolvedAttachment) -> bool:
        client = self._require_client()
        reply_context = self._require_reply_context(msg.thread_ts)

        try:
            if attachment.is_image:
                uploaded = await asyncio.to_thread(
                    client.upload_image,
                    file_path=attachment.actual_path,
                    to_user_id=reply_context.to_user_id,
                )
                await asyncio.to_thread(
                    client.send_image,
                    to_user_id=reply_context.to_user_id,
                    uploaded=uploaded,
                    context_token=reply_context.context_token,
                )
            else:
                uploaded = await asyncio.to_thread(
                    client.upload_file,
                    file_path=attachment.actual_path,
                    to_user_id=reply_context.to_user_id,
                )
                await asyncio.to_thread(
                    client.send_file,
                    to_user_id=reply_context.to_user_id,
                    file_name=attachment.filename,
                    uploaded=uploaded,
                    context_token=reply_context.context_token,
                )
            return True
        except Exception:
            logger.exception("[WeChat] failed to send attachment: %s", attachment.filename)
            return False

    def _resolve_client_kwargs(self) -> dict[str, str]:
        session = load_session_data(self.config.get("session_file"))
        configured_bot_token = str(self.config.get("bot_token", "")).strip()
        bot_token = configured_bot_token or os.getenv("WECHAT_BOT_TOKEN", "").strip() or str(session.get("bot_token", "")).strip()

        base_url = (
            str(self.config.get("base_url", "")).strip()
            or str(session.get("base_url", "")).strip()
            or str(session.get("baseurl", "")).strip()
            or DEFAULT_WECHAT_BASE_URL
        )
        channel_version = str(self.config.get("channel_version", DEFAULT_WECHAT_CHANNEL_VERSION)).strip() or DEFAULT_WECHAT_CHANNEL_VERSION
        return {
            "bot_token": bot_token,
            "base_url": base_url,
            "channel_version": channel_version,
        }

    def _create_client(self, *, bot_token: str, base_url: str, channel_version: str):
        from wechat_link import Client

        return Client(
            bot_token=bot_token,
            base_url=base_url,
            channel_version=channel_version,
        )

    def _create_cursor_store(self, path: Any):
        from wechat_link.store import FileCursorStore

        cursor_path = Path(path) if isinstance(path, str) and path.strip() else Path(".deer-flow") / "wechat-cursor.json"
        return FileCursorStore(cursor_path)

    def _create_polling_thread(self) -> threading.Thread:
        return threading.Thread(target=self._run_polling, daemon=True)

    def _run_polling(self) -> None:
        if not self._client:
            return

        cursor = ""
        if self._cursor_store is not None:
            try:
                cursor = self._cursor_store.load() or ""
            except Exception:
                logger.exception("Failed to load WeChat cursor")

        while self._running:
            try:
                updates = self._client.get_updates(cursor=cursor)
                next_cursor = getattr(updates, "next_cursor", None)
                if isinstance(next_cursor, str) and next_cursor:
                    cursor = next_cursor
                    if self._cursor_store is not None:
                        self._cursor_store.save(next_cursor)

                for message in getattr(updates, "messages", []):
                    inbound = self._build_inbound_from_message(message)
                    if inbound is None:
                        continue
                    if self._main_loop and self._main_loop.is_running():
                        asyncio.run_coroutine_threadsafe(self.bus.publish_inbound(inbound), self._main_loop)
            except Exception:
                if self._running:
                    logger.exception("WeChat polling error")
                    time.sleep(self._poll_interval_seconds)

    def _build_inbound_from_message(self, message: Any) -> InboundMessage | None:
        from_user_id = getattr(message, "from_user_id", None)
        context_token = getattr(message, "context_token", None)
        text_method = getattr(message, "text", None)
        raw_text = text_method() if callable(text_method) else getattr(message, "text", "")
        text = str(raw_text or "").strip()

        if not text or not from_user_id or not context_token:
            return None

        context_key = str(context_token)
        user_id = str(from_user_id)
        self._prune_reply_context()
        self._reply_context[context_key] = ReplyContext(
            to_user_id=user_id,
            context_token=context_key,
        )

        inbound = self._make_inbound(
            chat_id=user_id,
            user_id=user_id,
            text=text,
            msg_type=InboundMessageType.COMMAND if text.startswith("/") else InboundMessageType.CHAT,
            thread_ts=context_key,
            metadata={
                "context_token": context_key,
                "to_user_id": user_id,
                "raw_item_list": list(getattr(message, "item_list", []) or []),
            },
        )
        inbound.topic_id = None
        return inbound

    def _prune_reply_context(self) -> None:
        if not self._reply_context:
            return

        now = time.time()
        expired = [
            key
            for key, value in self._reply_context.items()
            if now - value.created_at > self._reply_context_ttl_seconds
        ]
        for key in expired:
            self._reply_context.pop(key, None)

    def _require_client(self):
        if self._client is None:
            raise RuntimeError("WeChat client is not initialized")
        return self._client

    def _require_reply_context(self, thread_ts: str | None) -> ReplyContext:
        self._prune_reply_context()
        if not thread_ts:
            raise RuntimeError("WeChat reply context requires thread_ts")

        reply_context = self._reply_context.get(thread_ts)
        if reply_context is None:
            raise RuntimeError(f"WeChat reply context not found for thread_ts={thread_ts}")
        return reply_context

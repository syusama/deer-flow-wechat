from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.channels.message_bus import InboundMessageType, MessageBus, OutboundMessage, ResolvedAttachment


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@dataclass
class _FakeUploadedMedia:
    kind: str


class _FakeWeChatMessage:
    def __init__(self, *, from_user_id: str | None, context_token: str | None, text: str, to_user_id: str = "bot@im.wechat"):
        self.from_user_id = from_user_id
        self.context_token = context_token
        self.to_user_id = to_user_id
        self._text = text
        self.item_list = [{"type": 1, "text_item": {"text": text}}]

    def text(self) -> str:
        return self._text


class _FakeWeChatClient:
    def __init__(self):
        self.sent_text: list[dict] = []
        self.uploaded_images: list[dict] = []
        self.sent_images: list[dict] = []
        self.uploaded_files: list[dict] = []
        self.sent_files: list[dict] = []
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def send_text(self, **kwargs):
        self.sent_text.append(kwargs)
        return "client-text-1"

    def upload_image(self, **kwargs):
        self.uploaded_images.append(kwargs)
        return _FakeUploadedMedia(kind="image")

    def send_image(self, **kwargs):
        self.sent_images.append(kwargs)
        return "client-image-1"

    def upload_file(self, **kwargs):
        self.uploaded_files.append(kwargs)
        return _FakeUploadedMedia(kind="file")

    def send_file(self, **kwargs):
        self.sent_files.append(kwargs)
        return "client-file-1"


class _FakeCursorStore:
    def __init__(self):
        self.saved: list[str] = []

    def load(self) -> str | None:
        return None

    def save(self, cursor: str) -> None:
        self.saved.append(cursor)


class _FakeThread:
    def __init__(self):
        self.started = False
        self.joined = False

    def start(self) -> None:
        self.started = True

    def join(self, timeout=None) -> None:
        self.joined = True


class TestWeChatChannel:
    def test_start_uses_session_file_bot_token_when_config_missing(self, tmp_path, monkeypatch):
        from app.channels.wechat import WeChatChannel

        session_path = tmp_path / "wechat-session.json"
        session_path.write_text(json.dumps({"bot_token": "session-token", "base_url": "https://session.example"}), encoding="utf-8")

        bus = MessageBus()
        channel = WeChatChannel(
            bus=bus,
            config={
                "session_file": str(session_path),
                "cursor_file": str(tmp_path / "cursor.json"),
            },
        )

        fake_client = _FakeWeChatClient()
        fake_store = _FakeCursorStore()
        fake_thread = _FakeThread()
        client_args = {}

        def fake_create_client(*, bot_token: str, base_url: str, channel_version: str):
            client_args.update(
                {
                    "bot_token": bot_token,
                    "base_url": base_url,
                    "channel_version": channel_version,
                }
            )
            return fake_client

        monkeypatch.setattr(channel, "_create_client", fake_create_client)
        monkeypatch.setattr(channel, "_create_cursor_store", lambda path: fake_store)
        monkeypatch.setattr(channel, "_create_polling_thread", lambda: fake_thread)

        async def go():
            await channel.start()
            assert channel.is_running is True
            assert fake_thread.started is True
            assert client_args["bot_token"] == "session-token"
            assert client_args["base_url"] == "https://session.example"
            await channel.stop()
            assert fake_client.closed is True
            assert fake_thread.joined is True

        _run(go())

    def test_start_without_token_keeps_channel_stopped(self, tmp_path):
        from app.channels.wechat import WeChatChannel

        bus = MessageBus()
        channel = WeChatChannel(
            bus=bus,
            config={
                "session_file": str(tmp_path / "missing-session.json"),
                "cursor_file": str(tmp_path / "cursor.json"),
            },
        )

        async def go():
            await channel.start()
            assert channel.is_running is False

        _run(go())

    def test_build_inbound_uses_context_token_as_thread_key(self):
        from app.channels.wechat import WeChatChannel

        channel = WeChatChannel(bus=MessageBus(), config={})
        message = _FakeWeChatMessage(from_user_id="user@im.wechat", context_token="ctx-123", text="/status")

        inbound = channel._build_inbound_from_message(message)

        assert inbound is not None
        assert inbound.chat_id == "user@im.wechat"
        assert inbound.user_id == "user@im.wechat"
        assert inbound.thread_ts == "ctx-123"
        assert inbound.msg_type == InboundMessageType.COMMAND
        assert inbound.metadata["context_token"] == "ctx-123"
        assert channel._reply_context["ctx-123"].to_user_id == "user@im.wechat"

    def test_build_inbound_ignores_empty_or_incomplete_messages(self):
        from app.channels.wechat import WeChatChannel

        channel = WeChatChannel(bus=MessageBus(), config={})

        assert channel._build_inbound_from_message(_FakeWeChatMessage(from_user_id="user@im.wechat", context_token="ctx-1", text="   ")) is None
        assert channel._build_inbound_from_message(_FakeWeChatMessage(from_user_id=None, context_token="ctx-2", text="hello")) is None
        assert channel._build_inbound_from_message(_FakeWeChatMessage(from_user_id="user@im.wechat", context_token=None, text="hello")) is None

    def test_send_uses_reply_context_from_thread_ts(self):
        from app.channels.wechat import ReplyContext, WeChatChannel

        channel = WeChatChannel(bus=MessageBus(), config={})
        fake_client = _FakeWeChatClient()
        channel._client = fake_client
        channel._reply_context["ctx-123"] = ReplyContext(to_user_id="user@im.wechat", context_token="ctx-123")

        async def go():
            await channel.send(
                OutboundMessage(
                    channel_name="wechat",
                    chat_id="user@im.wechat",
                    thread_id="thread-1",
                    text="hello from deerflow",
                    thread_ts="ctx-123",
                )
            )

        _run(go())

        assert fake_client.sent_text == [
            {
                "to_user_id": "user@im.wechat",
                "text": "hello from deerflow",
                "context_token": "ctx-123",
            }
        ]

    def test_send_file_dispatches_images_and_generic_files(self, tmp_path):
        from app.channels.wechat import ReplyContext, WeChatChannel

        image_path = tmp_path / "chart.png"
        image_path.write_bytes(b"\x89PNG")
        file_path = tmp_path / "report.pdf"
        file_path.write_bytes(b"%PDF")

        image_attachment = ResolvedAttachment(
            virtual_path="/mnt/user-data/outputs/chart.png",
            actual_path=image_path,
            filename="chart.png",
            mime_type="image/png",
            size=4,
            is_image=True,
        )
        file_attachment = ResolvedAttachment(
            virtual_path="/mnt/user-data/outputs/report.pdf",
            actual_path=file_path,
            filename="report.pdf",
            mime_type="application/pdf",
            size=4,
            is_image=False,
        )

        channel = WeChatChannel(bus=MessageBus(), config={})
        fake_client = _FakeWeChatClient()
        channel._client = fake_client
        channel._reply_context["ctx-123"] = ReplyContext(to_user_id="user@im.wechat", context_token="ctx-123")
        message = OutboundMessage(channel_name="wechat", chat_id="user@im.wechat", thread_id="thread-1", text="done", thread_ts="ctx-123")

        async def go():
            image_result = await channel.send_file(message, image_attachment)
            file_result = await channel.send_file(message, file_attachment)
            assert image_result is True
            assert file_result is True

        _run(go())

        assert fake_client.uploaded_images[0]["file_path"] == image_path
        assert fake_client.sent_images[0]["to_user_id"] == "user@im.wechat"
        assert fake_client.sent_images[0]["context_token"] == "ctx-123"
        assert fake_client.uploaded_files[0]["file_path"] == file_path
        assert fake_client.sent_files[0]["file_name"] == "report.pdf"

    def test_send_raises_when_reply_context_missing(self):
        from app.channels.wechat import WeChatChannel

        channel = WeChatChannel(bus=MessageBus(), config={})
        channel._client = _FakeWeChatClient()

        async def go():
            with pytest.raises(RuntimeError, match="reply context"):
                await channel.send(
                    OutboundMessage(
                        channel_name="wechat",
                        chat_id="user@im.wechat",
                        thread_id="thread-1",
                        text="hello",
                        thread_ts="ctx-missing",
                    )
                )

        _run(go())

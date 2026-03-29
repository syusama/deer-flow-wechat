from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx


def _status(
    status: str,
    *,
    bot_token: str | None = None,
    baseurl: str | None = None,
    ilink_bot_id: str | None = None,
    ilink_user_id: str | None = None,
):
    return SimpleNamespace(
        status=status,
        bot_token=bot_token,
        baseurl=baseurl,
        ilink_bot_id=ilink_bot_id,
        ilink_user_id=ilink_user_id,
    )


class _FakeLoginClient:
    def __init__(self, *, status_sequence):
        self._status_sequence = list(status_sequence)
        self.closed = False
        self.get_bot_qrcode_calls = 0
        self.saved_qr_paths: list[Path] = []
        self.printed_qr_contents: list[str] = []

    def get_bot_qrcode(self):
        self.get_bot_qrcode_calls += 1
        return SimpleNamespace(
            qrcode=f"qr-{self.get_bot_qrcode_calls}",
            qrcode_img_content=f"img-{self.get_bot_qrcode_calls}",
        )

    def save_qrcode_image(self, qrcode_img_content: str, *, output_path: str | Path):
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(qrcode_img_content, encoding="utf-8")
        self.saved_qr_paths.append(path)
        return path

    def print_qrcode_terminal(self, qrcode_img_content: str):
        self.printed_qr_contents.append(qrcode_img_content)
        return qrcode_img_content

    def get_qrcode_status(self, qrcode: str):
        next_value = self._status_sequence.pop(0)
        if isinstance(next_value, Exception):
            raise next_value
        return next_value

    def close(self):
        self.closed = True


class TestWeChatSessionHelpers:
    def test_write_and_load_session_round_trip(self, tmp_path):
        from app.channels.wechat_session import build_session_payload, load_session_data, write_session_data

        session_path = tmp_path / ".state" / "wechat-session.json"
        payload = build_session_payload(
            bot_token="session-token",
            base_url="https://wechat.example",
            ilink_bot_id="bot-1",
            ilink_user_id="user-1",
            updated_at=123.0,
        )

        written_path = write_session_data(session_path, payload)

        assert written_path == session_path
        assert load_session_data(session_path) == payload

    def test_load_session_data_returns_empty_for_missing_or_invalid_file(self, tmp_path):
        from app.channels.wechat_session import load_session_data

        missing_path = tmp_path / "missing.json"
        invalid_path = tmp_path / "invalid.json"
        invalid_path.write_text("{not-json", encoding="utf-8")

        assert load_session_data(missing_path) == {}
        assert load_session_data(invalid_path) == {}


class TestWeChatLoginFlow:
    def test_run_login_flow_saves_confirmed_session_and_closes_client(self, tmp_path):
        from app.channels.wechat_login import run_login_flow

        session_path = tmp_path / ".state" / "wechat-session.json"
        qr_image_path = tmp_path / ".state" / "wechat-login-qrcode.png"
        fake_client = _FakeLoginClient(
            status_sequence=[
                _status(
                    "confirmed",
                    bot_token="confirmed-token",
                    baseurl="https://wechat.example",
                    ilink_bot_id="bot-1",
                    ilink_user_id="user-1",
                )
            ]
        )

        result = run_login_flow(
            client=fake_client,
            session_file=session_path,
            qr_image_path=qr_image_path,
            poll_interval_seconds=0.0,
            sleep=lambda _seconds: None,
            write=lambda _message: None,
        )

        assert result == session_path
        session_payload = json.loads(session_path.read_text(encoding="utf-8"))
        assert session_payload["bot_token"] == "confirmed-token"
        assert session_payload["base_url"] == "https://wechat.example"
        assert session_payload["ilink_bot_id"] == "bot-1"
        assert session_payload["ilink_user_id"] == "user-1"
        assert isinstance(session_payload["updated_at"], float)
        assert fake_client.saved_qr_paths == [qr_image_path]
        assert fake_client.printed_qr_contents == ["img-1"]
        assert fake_client.closed is True

    def test_run_login_flow_refreshes_expired_qrcode_before_confirming(self, tmp_path):
        from app.channels.wechat_login import run_login_flow

        session_path = tmp_path / "wechat-session.json"
        qr_image_path = tmp_path / "wechat-login-qrcode.png"
        fake_client = _FakeLoginClient(
            status_sequence=[
                _status("expired"),
                _status("confirmed", bot_token="confirmed-token"),
            ]
        )

        run_login_flow(
            client=fake_client,
            session_file=session_path,
            qr_image_path=qr_image_path,
            poll_interval_seconds=0.0,
            sleep=lambda _seconds: None,
            write=lambda _message: None,
        )

        assert fake_client.get_bot_qrcode_calls == 2
        assert fake_client.saved_qr_paths == [qr_image_path, qr_image_path]
        session_payload = json.loads(session_path.read_text(encoding="utf-8"))
        assert session_payload["bot_token"] == "confirmed-token"
        assert session_payload["base_url"] == "https://ilinkai.weixin.qq.com"

    def test_run_login_flow_retries_after_timeout(self, tmp_path):
        from app.channels.wechat_login import run_login_flow

        session_path = tmp_path / "wechat-session.json"
        qr_image_path = tmp_path / "wechat-login-qrcode.png"
        fake_client = _FakeLoginClient(
            status_sequence=[
                httpx.TimeoutException("timeout"),
                _status("confirmed", bot_token="confirmed-token"),
            ]
        )

        run_login_flow(
            client=fake_client,
            session_file=session_path,
            qr_image_path=qr_image_path,
            poll_interval_seconds=0.0,
            sleep=lambda _seconds: None,
            write=lambda _message: None,
        )

        session_payload = json.loads(session_path.read_text(encoding="utf-8"))
        assert session_payload["bot_token"] == "confirmed-token"
        assert fake_client.closed is True

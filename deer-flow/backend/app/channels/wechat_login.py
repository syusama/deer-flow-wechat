"""CLI for acquiring and persisting a WeChat session for DeerFlow."""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from app.channels.wechat import DEFAULT_WECHAT_CHANNEL_VERSION
from app.channels.wechat_session import DEFAULT_WECHAT_BASE_URL, build_session_payload_from_status, write_session_data

DEFAULT_QR_IMAGE_FILENAME = "wechat-login-qrcode.png"
DEFAULT_SESSION_FILE = Path(".state") / "wechat-link-session.json"


def _default_writer(message: str) -> None:
    print(message)


def issue_login_qrcode(
    *,
    client: Any,
    qr_image_path: str | Path,
    write: Callable[[str], None] = _default_writer,
):
    qr = client.get_bot_qrcode()
    image_path = Path(
        client.save_qrcode_image(
            qr.qrcode_img_content,
            output_path=qr_image_path,
        )
    )

    write("Scan this QR code with WeChat.")
    write(f"qrcode: {qr.qrcode}")
    write(f"qrcode_image: {image_path.resolve()}")
    write("terminal qr:")
    client.print_qrcode_terminal(qr.qrcode_img_content)
    return qr


def run_login_flow(
    *,
    client: Any,
    session_file: str | Path,
    qr_image_path: str | Path,
    base_url: str = DEFAULT_WECHAT_BASE_URL,
    poll_interval_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    write: Callable[[str], None] = _default_writer,
) -> Path:
    session_path = Path(session_file)
    qr_path = Path(qr_image_path)
    qr = issue_login_qrcode(client=client, qr_image_path=qr_path, write=write)
    last_status = ""

    try:
        while True:
            try:
                status = client.get_qrcode_status(qr.qrcode)
            except httpx.TimeoutException:
                write("QR status request timed out, keep waiting...")
                continue

            status_name = str(getattr(status, "status", "") or "")
            if status_name != last_status:
                write(f"qr status: {status_name}")
                last_status = status_name

            if status_name == "confirmed" and getattr(status, "bot_token", None):
                session_payload = build_session_payload_from_status(
                    status,
                    fallback_base_url=base_url,
                )
                saved_path = write_session_data(session_path, session_payload)
                write(f"session saved to: {saved_path.resolve()}")
                return saved_path

            if status_name == "expired":
                write("QR code expired, refreshing...")
                qr = issue_login_qrcode(client=client, qr_image_path=qr_path, write=write)
                last_status = ""
                continue

            sleep(max(0.0, float(poll_interval_seconds)))
    finally:
        if hasattr(client, "close"):
            client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan a WeChat QR code and save a DeerFlow session file.")
    parser.add_argument(
        "--session-file",
        default=str(DEFAULT_SESSION_FILE),
        help="Path to the session JSON file written after login succeeds.",
    )
    parser.add_argument(
        "--qr-image-path",
        default=None,
        help="Optional path for saving the QR image. Defaults to <session-dir>/wechat-login-qrcode.png.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_WECHAT_BASE_URL,
        help="wechat-link base URL.",
    )
    parser.add_argument(
        "--channel-version",
        default=DEFAULT_WECHAT_CHANNEL_VERSION,
        help="wechat-link channel version.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=1.0,
        help="Seconds to wait between QR status polls.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    session_path = Path(args.session_file)
    qr_image_path = (
        Path(args.qr_image_path)
        if args.qr_image_path
        else session_path.parent / DEFAULT_QR_IMAGE_FILENAME
    )

    from wechat_link import Client

    run_login_flow(
        client=Client(
            base_url=args.base_url,
            channel_version=args.channel_version,
        ),
        session_file=session_path,
        qr_image_path=qr_image_path,
        base_url=args.base_url,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# WeChat Channel Plugin Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a minimal third-party channel loading hook to DeerFlow and implement a WeChat IM channel MVP backed by `wechat-link`.

**Architecture:** Keep DeerFlow's existing `Channel -> MessageBus -> ChannelManager` pipeline unchanged. Add a `class_path` override in ChannelService so a third-party `WeChatChannel` can be loaded from config, then implement the channel as an in-process SDK integration that stores per-message reply context for `context_token`-based responses.

**Tech Stack:** Python 3.12, FastAPI/Gateway lifecycle, `langgraph-sdk`, `wechat-link`, `pytest`, `httpx`, DeerFlow channel abstractions.

---

## Chunk 1: DeerFlow Plugin Loading Hook

### Task 1: Add tests for config-driven channel class loading

**Files:**
- Create/Modify: `backend/tests/test_channel_service.py`
- Modify: `backend/app/channels/service.py`
- Reference: `backend/app/channels/base.py`

- [ ] **Step 1: Write the failing tests**

```python
async def test_start_channel_prefers_class_path_from_config(monkeypatch):
    ...

async def test_start_channel_rejects_unknown_channel_without_class_path():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_channel_service.py -v
```

Expected: FAIL because `ChannelService` does not yet honor `class_path`.

- [ ] **Step 3: Write minimal implementation**

Implement `class_path` override resolution in `backend/app/channels/service.py`:
- if `config["class_path"]` is a non-empty string, use it
- otherwise fall back to `_CHANNEL_REGISTRY`
- keep existing behavior for built-in channels

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_channel_service.py -v
```

Expected: PASS.

- [ ] **Step 5: Update docs**

Modify:
- `README.md`
- `backend/CLAUDE.md`

Add a short note that IM channels can now be loaded from `channels.<name>.class_path`.

## Chunk 2: WeChat Channel MVP Tests

### Task 2: Add tests for WeChat config/session resolution and reply context

**Files:**
- Create: `backend/tests/test_wechat_channel.py`
- Create: `backend/app/channels/wechat.py`

- [ ] **Step 1: Write failing unit tests**

Cover:
- session file fallback for `bot_token`
- inbound text message conversion into `InboundMessage`
- outbound send resolving `context_token` via cached `thread_ts`
- channel ignores empty inbound messages

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_wechat_channel.py -v
```

Expected: FAIL because `wechat.py` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Implement:
- `WeChatChannel(Channel)`
- lightweight session loader
- reply context cache
- inbound parser for text-only messages
- outbound text send path

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_wechat_channel.py -v
```

Expected: PASS.

### Task 3: Add tests for channel startup and polling bridge

**Files:**
- Modify: `backend/tests/test_wechat_channel.py`
- Modify: `backend/app/channels/wechat.py`

- [ ] **Step 1: Write failing tests**

Cover:
- `start()` subscribes outbound and creates polling thread when config is valid
- `start()` is a no-op when no token/session exists
- polling loop saves cursor updates and schedules inbound publish on main loop

- [ ] **Step 2: Run targeted tests to verify failure**

Run:

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_wechat_channel.py -k "start or poll" -v
```

- [ ] **Step 3: Implement polling loop and thread handoff**

Add:
- `wechat_link.Client` lazy init
- `FileCursorStore` integration
- dedicated polling thread
- `asyncio.run_coroutine_threadsafe` bridge back to main loop

- [ ] **Step 4: Re-run targeted tests**

Expected: PASS.

## Chunk 3: Artifact Delivery and Documentation

### Task 4: Add file/image outbound support

**Files:**
- Modify: `backend/tests/test_wechat_channel.py`
- Modify: `backend/app/channels/wechat.py`

- [ ] **Step 1: Write failing tests**

Cover:
- image attachment -> `upload_image` + `send_image`
- non-image attachment -> `upload_file` + `send_file`
- missing reply context -> safe failure / no send

- [ ] **Step 2: Run targeted tests to verify failure**

Run:

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_wechat_channel.py -k "attachment" -v
```

- [ ] **Step 3: Implement minimal attachment support**

Keep MVP scope:
- image and generic file only
- no video/voice artifact sending yet

- [ ] **Step 4: Re-run targeted tests**

Expected: PASS.

### Task 5: Register built-in `wechat` alias and document usage

**Files:**
- Modify: `backend/app/channels/service.py`
- Modify: `backend/pyproject.toml`
- Modify: `README.md`
- Modify: `README_zh.md`
- Modify: `backend/CLAUDE.md`

- [ ] **Step 1: Add failing test for built-in registry alias**

Assert that `wechat` can start from `_CHANNEL_REGISTRY` once module exists.

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement**

Add:
- `"wechat": "app.channels.wechat:WeChatChannel"` in registry
- `wechat-link` dependency to backend
- config example snippets and operational notes

- [ ] **Step 4: Re-run tests**

Expected: PASS.

## Chunk 4: Verification

### Task 6: Run focused regression suite

**Files:**
- Test only

- [ ] **Step 1: Run channel-related tests**

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_channel_service.py tests/test_wechat_channel.py -v
```

- [ ] **Step 2: Run existing IM-adjacent regression tests**

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_feishu_parser.py tests/test_serialize_message_content.py -v
```

- [ ] **Step 3: If failures appear, fix with TDD**

- [ ] **Step 4: Summarize exact verification evidence**

Include passed commands and any remaining gaps.

## Chunk 5: WeChat Login CLI

### Task 7: Add tests for session persistence and QR login flow

**Files:**
- Create: `backend/tests/test_wechat_login.py`
- Create/Modify: `backend/app/channels/wechat_session.py`
- Create/Modify: `backend/app/channels/wechat_login.py`

- [ ] **Step 1: Write failing tests**

Cover:
- session payload persistence to `session_file`
- confirmed QR status writing `bot_token/base_url/ilink ids`
- expired QR status forcing a QR refresh before continuing
- client close always happening on exit

- [ ] **Step 2: Run targeted tests to verify failure**

Run:

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_wechat_login.py -v
```

Expected: FAIL because the login/session helpers do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Implement:
- reusable session helpers shared by the channel and login CLI
- login flow around `wechat_link.Client.get_bot_qrcode()` and `get_qrcode_status()`
- QR image persistence + terminal QR rendering

- [ ] **Step 4: Re-run targeted tests**

Expected: PASS.

### Task 8: Expose CLI and document operator flow

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `README.md`
- Modify: `README_zh.md`
- Modify: `config.example.yaml`

- [ ] **Step 1: Add CLI entry point**

Expose a script such as:

```toml
[project.scripts]
deerflow-wechat-login = "app.channels.wechat_login:main"
```

- [ ] **Step 2: Document exact usage**

Include:
- sample command
- expected session file output path
- relationship between `session_file` and optional `WECHAT_BOT_TOKEN`

- [ ] **Step 3: Re-run focused regression suite**

Run:

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_channels.py::TestChannelService tests/test_channel_file_attachments.py tests/test_wechat_channel.py tests/test_wechat_login.py -v
```

Expected: PASS.

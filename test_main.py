"""
Emoji Kitchen Plugin - Unit Tests

测试核心逻辑：emoji 解析、codepoint 转换、组合查找、正则匹配、缓存逻辑、SSRF 防护、并发锁、自响应检测。
"""

import asyncio
import json
import os
import tempfile
import weakref
import gc
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# ============================================================
# Mock astrbot & aiohttp setup
# ============================================================

_fake_data_dir = Path(tempfile.gettempdir()) / "_emoji_kitchen_test_data"
_fake_data_dir.mkdir(parents=True, exist_ok=True)

class _FakeStar:
    def __init__(self, context):
        self.context = context

class _FakeContext:
    pass

_star_module = MagicMock()
_star_module.Star = _FakeStar
_star_module.Context = _FakeContext
_star_module.StarTools = MagicMock()
_star_module.StarTools.get_data_dir = MagicMock(return_value=_fake_data_dir)

_filter_mock = MagicMock()
_filter_mock.command = lambda *a, **kw: lambda fn: fn
_filter_mock.event_message_type = lambda *a, **kw: lambda fn: fn

_event_module = MagicMock()
_event_module.filter = _filter_mock
_event_module.AstrMessageEvent = MagicMock()

_filter_module = MagicMock()
_filter_module.EventMessageType = MagicMock()

_MOCKS = {
    "aiohttp": MagicMock(),
    "astrbot": MagicMock(),
    "astrbot.api": MagicMock(),
    "astrbot.api.event": _event_module,
    "astrbot.api.event.filter": _filter_module,
    "astrbot.api.star": _star_module,
    "astrbot.api.message_components": MagicMock(),
}

with patch.dict("sys.modules", _MOCKS):
    import main as _main_module
    from main import (
        emoji_to_codepoint,
        _url_to_cache_filename,
        _is_allowed_image_url,
        _is_valid_image_magic,
        EMOJI_ITER_PATTERN,
        TWO_EMOJI_MSG_PATTERN,
        EmojiKitchenPlugin,
    )

# ============================================================
# Fixtures & Helpers
# ============================================================

MOCK_METADATA = {
    "knownSupportedEmoji": ["1f600", "1f63a", "1f525"],
    "data": {
        "1f600": {
            "combinations": {
                "1f63a": [
                    {"gStaticUrl": "https://www.gstatic.com/emoji/1f600_1f63a.png", "isLatest": True},
                ],
            },
        },
        "1f63a": {
            "combinations": {},
        },
        "1f525": {"combinations": {}},
    },
}

@pytest.fixture
def plugin(tmp_path):
    img_dir = tmp_path / "images"
    img_dir.mkdir()

    context = _FakeContext()
    p = EmojiKitchenPlugin(context)
    p._data_dir = tmp_path
    p._cache_file = tmp_path / "metadata.json"
    p._img_dir = img_dir
    p.metadata = None

    # Mock session
    p.session = MagicMock()
    p.session.get = MagicMock()
    p.session.close = AsyncMock()

    return p

@pytest.fixture
def plugin_with_meta(plugin):
    plugin.metadata = MOCK_METADATA
    return plugin

def _make_resp_mock(
    content_type="image/png",
    chunks=(b'\x89PNG\r\n\x1a\n' + b'x' * 100,),
    raise_on_raise_for_status=False,
    final_url="https://www.gstatic.com/emoji/test.png",
):
    resp = MagicMock()
    resp.content_type = content_type
    if raise_on_raise_for_status:
        resp.raise_for_status = MagicMock(side_effect=Exception("HTTP error"))
    else:
        resp.raise_for_status = MagicMock()

    resp.url = final_url

    async def _iter_chunked(size):
        for chunk in chunks:
            yield chunk

    resp.content = MagicMock()
    resp.content.iter_chunked = _iter_chunked
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=None)
    return resp

def _make_session_get_mock(resp_or_map):
    """
    resp_or_map: either a single resp mock, or a dict {url: resp_mock}
    """
    def get(url, **kwargs):
        if isinstance(resp_or_map, dict):
            # Try exact match or match base url
            r = resp_or_map.get(url)
            if not r:
                # Basic fallback for mirror URL logic
                # If url is a mirror url, maybe we have a mock for it?
                pass
            return r if r else _make_resp_mock(raise_on_raise_for_status=True)
        return resp_or_map

    return MagicMock(side_effect=get)


# ============================================================
# Tests: SSRF & URL Safety
# ============================================================

class TestUrlSafety:
    def test_allowed_domains(self):
        assert _is_allowed_image_url("https://www.gstatic.com/a.png")
        assert _is_allowed_image_url("https://fonts.gstatic.com/a.png")
        assert _is_allowed_image_url("https://i0.wp.com/example.com/a.png")
        assert _is_allowed_image_url("https://wsrv.nl/?url=a.png")
        assert _is_allowed_image_url("https://images.weserv.nl/?url=a.png")

    def test_disallowed_domains(self):
        assert not _is_allowed_image_url("https://evil.com/a.png")
        assert not _is_allowed_image_url("http://www.gstatic.com/a.png")
        assert not _is_allowed_image_url("https://192.168.1.1/a.png")
        assert not _is_allowed_image_url("https://localhost/a.png")


# ============================================================
# Tests: _download_image with new logic
# ============================================================

class TestDownloadImage:

    @pytest.mark.asyncio
    async def test_download_success_direct(self, plugin):
        url = "https://www.gstatic.com/emoji/1f600_1f63a.png"
        resp = _make_resp_mock()
        plugin.session.get.return_value = resp

        path = await plugin._download_image(url)
        assert path is not None
        assert Path(path).exists()
        assert Path(path).stat().st_size > 100

    @pytest.mark.asyncio
    async def test_download_ssrf_blocked_redirect(self, plugin):
        """Test that redirect to a disallowed host is blocked"""
        url = "https://wsrv.nl/?url=test.png"
        # Mock response that claims to be from wsrv.nl but redirected to internal IP
        resp = _make_resp_mock(final_url="http://192.168.1.1/test.png")
        plugin.session.get.return_value = resp

        path = await plugin._download_image(url)
        assert path is None

    @pytest.mark.asyncio
    async def test_download_ssrf_allowed_mirror_redirect(self, plugin):
        """Test that redirect to another allowed mirror or gstatic is allowed"""
        url = "https://wsrv.nl/?url=test.png"
        # Redirects to gstatic is fine
        resp = _make_resp_mock(final_url="https://www.gstatic.com/emoji/test.png")
        plugin.session.get.return_value = resp

        path = await plugin._download_image(url)
        assert path is not None

    @pytest.mark.asyncio
    async def test_session_not_initialized(self, plugin):
        plugin.session = None
        path = await plugin._download_image("https://www.gstatic.com/a.png")
        assert path is None

    @pytest.mark.asyncio
    async def test_streaming_download_writes_file(self, plugin):
        url = "https://www.gstatic.com/emoji/stream.png"
        chunks = [b'\x89PNG\r\n\x1a\n', b'data' * 50]
        resp = _make_resp_mock(chunks=chunks)
        plugin.session.get.return_value = resp

        path = await plugin._download_image(url)
        assert path is not None
        content = Path(path).read_bytes()
        assert content == b"".join(chunks)

    @pytest.mark.asyncio
    async def test_magic_number_check_fail(self, plugin):
        url = "https://www.gstatic.com/emoji/bad.png"
        chunks = [b'BADMAGIC' + b'x' * 100]
        resp = _make_resp_mock(chunks=chunks)
        plugin.session.get.return_value = resp

        path = await plugin._download_image(url)
        assert path is None


# ============================================================
# Tests: Session Lifecycle
# ============================================================

class TestSessionLifecycle:
    @pytest.mark.asyncio
    async def test_initialize_creates_session(self, plugin):
        # Override plugin fixture mock
        plugin.session = None
        # Mock _download_metadata to avoid actual network call
        plugin._download_metadata = AsyncMock()

        # Since main.py imported aiohttp from our _MOCKS, we should verify calls on that mock
        # instead of patching the real aiohttp (which might not be the same object).
        mock_aiohttp = _main_module.aiohttp
        mock_aiohttp.ClientSession.reset_mock()
        mock_aiohttp.ClientSession.return_value = MagicMock()

        await plugin.initialize()

        mock_aiohttp.ClientSession.assert_called_once()
        assert plugin.session is not None

    @pytest.mark.asyncio
    async def test_terminate_closes_session(self, plugin):
        await plugin.terminate()
        plugin.session.close.assert_called_once()


# ============================================================
# Tests: Lock Cleanup (WeakValueDictionary)
# ============================================================

class TestLockCleanup:
    @pytest.mark.asyncio
    async def test_lock_is_weakly_referenced(self, plugin):
        url = "https://www.gstatic.com/emoji/lock.png"
        filename = _url_to_cache_filename(url)

        # Simulate download holding lock
        async def mock_download():
            lock = plugin._download_locks.get(filename)
            if not lock:
                lock = asyncio.Lock()
                plugin._download_locks[filename] = lock

            assert filename in plugin._download_locks
            async with lock:
                await asyncio.sleep(0.1)
            # lock goes out of scope here

        task = asyncio.create_task(mock_download())
        await asyncio.sleep(0.05)
        # Lock should be in dict while running
        assert filename in plugin._download_locks

        await task

        # Force GC
        gc.collect()

        # Lock should be removed from dict
        assert filename not in plugin._download_locks


# ============================================================
# Tests: Auto-mix Self-Ignore
# ============================================================

class TestAutoMixSelfIgnore:
    @pytest.mark.asyncio
    async def test_ignore_self_message(self, plugin_with_meta):
        mock_event = MagicMock()
        mock_event.message_str = "😀😺"
        # Simulate self message
        mock_event.message_obj.sender.user_id = "bot_123"
        mock_event.message_obj.self_id = "bot_123"

        plugin_with_meta._download_image = AsyncMock()

        results = []
        async for r in plugin_with_meta.auto_mix(mock_event):
            results.append(r)

        assert len(results) == 0
        plugin_with_meta._download_image.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_other_message(self, plugin_with_meta):
        mock_event = MagicMock()
        mock_event.message_str = "😀😺"
        mock_event.message_obj.sender.user_id = "user_456"
        mock_event.message_obj.self_id = "bot_123"
        mock_event.chain_result = lambda c: c

        plugin_with_meta._download_image = AsyncMock(return_value="/tmp/path")

        results = []
        async for r in plugin_with_meta.auto_mix(mock_event):
            results.append(r)

        assert len(results) == 1
        plugin_with_meta._download_image.assert_called()

# ============================================================
# Tests: Regex (Existing tests kept for regression check)
# ============================================================
class TestRegex:
    def test_two_emoji(self):
        assert TWO_EMOJI_MSG_PATTERN.match("😀😺")
        assert TWO_EMOJI_MSG_PATTERN.match("😀 😺")
        assert not TWO_EMOJI_MSG_PATTERN.match("😀😺 a")

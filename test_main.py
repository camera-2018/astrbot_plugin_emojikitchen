"""
Emoji Kitchen Plugin - Unit Tests

测试核心逻辑：emoji 解析、codepoint 转换、组合查找、正则匹配、缓存逻辑、SSRF 防护、并发锁、自响应检测。
"""

import asyncio
import tempfile
import gc
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ============================================================
# Mock astrbot & aiohttp setup
# ============================================================

_fake_data_dir = Path(tempfile.mkdtemp(prefix="_emoji_kitchen_test_data_"))

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
        _is_mirror_target_allowed,
        _is_valid_image_magic,
        _codepoint_variants,
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


# ============================================================
# Tests: SSRF & URL Safety
# ============================================================

class TestUrlSafety:
    def test_allowed_domains(self):
        assert _is_allowed_image_url("https://www.gstatic.com/a.png")
        assert _is_allowed_image_url("https://fonts.gstatic.com/a.png")
        assert _is_allowed_image_url("https://i0.wp.com/www.gstatic.com/a.png")
        assert _is_allowed_image_url("https://wsrv.nl/?url=www.gstatic.com/a.png")
        assert _is_allowed_image_url("https://images.weserv.nl/?url=www.gstatic.com/a.png")

    def test_disallowed_domains(self):
        assert not _is_allowed_image_url("https://evil.com/a.png")
        assert not _is_allowed_image_url("http://www.gstatic.com/a.png")
        assert not _is_allowed_image_url("https://192.168.1.1/a.png")
        assert not _is_allowed_image_url("https://localhost/a.png")

    def test_mirror_ssrf_blocked(self):
        """Mirror URLs pointing to non-gstatic targets must be blocked."""
        assert not _is_allowed_image_url("https://wsrv.nl/?url=https://internal.corp/secret")
        assert not _is_allowed_image_url("https://wsrv.nl/?url=http://192.168.1.1/a.png")
        assert not _is_allowed_image_url("https://images.weserv.nl/?url=evil.com/a.png")
        assert not _is_allowed_image_url("https://i0.wp.com/evil.com/a.png")
        # No url param at all
        assert not _is_allowed_image_url("https://wsrv.nl/")

    def test_mirror_gstatic_target_allowed(self):
        """Mirror URLs pointing to *.gstatic.com targets must be allowed."""
        assert _is_allowed_image_url("https://wsrv.nl/?url=https://www.gstatic.com/emoji/test.png")
        assert _is_allowed_image_url("https://images.weserv.nl/?url=www.gstatic.com/emoji/test.png")
        assert _is_allowed_image_url("https://i0.wp.com/www.gstatic.com/emoji/test.png")


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
        url = "https://wsrv.nl/?url=www.gstatic.com/emoji/test.png"
        # Mock response that claims to be from wsrv.nl but redirected to internal IP
        resp = _make_resp_mock(final_url="http://192.168.1.1/test.png")
        plugin.session.get.return_value = resp

        path = await plugin._download_image(url)
        assert path is None

    @pytest.mark.asyncio
    async def test_download_ssrf_allowed_mirror_redirect(self, plugin):
        """Test that redirect to another allowed mirror or gstatic is allowed"""
        url = "https://wsrv.nl/?url=www.gstatic.com/emoji/test.png"
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

    @pytest.mark.asyncio
    async def test_magic_number_check_small_first_chunk(self, plugin):
        """First chunk < 12 bytes should not bypass magic number validation."""
        url = "https://www.gstatic.com/emoji/small_chunk.png"
        # Split bad magic across small chunks; total > 12 bytes but not a valid image
        chunks = [b'BAD', b'MAGIC000', b'x' * 100]
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
    async def test_initialize_closes_old_session(self, plugin):
        """Re-initializing should close the previous session to prevent leaks."""
        old_session = MagicMock()
        old_session.closed = False
        old_session.close = AsyncMock()
        plugin.session = old_session
        plugin._download_metadata = AsyncMock()

        mock_aiohttp = _main_module.aiohttp
        mock_aiohttp.ClientSession.reset_mock()
        mock_aiohttp.ClientSession.return_value = MagicMock()

        await plugin.initialize()

        old_session.close.assert_called_once()
        assert plugin.session is not old_session

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

    def test_two_emoji_with_redundant_fe0f(self):
        """Fully-qualified emoji (with redundant FE0F) should still match."""
        # 😀\uFE0F😺 - grinning face with redundant FE0F + cat
        assert TWO_EMOJI_MSG_PATTERN.match("\U0001F600\uFE0F\U0001F63A")


# ============================================================
# Tests: Utility Functions
# ============================================================

class TestEmojiToCodepoint:
    def test_simple_emoji(self):
        assert emoji_to_codepoint("\U0001F600") == "1f600"

    def test_emoji_with_fe0f(self):
        assert emoji_to_codepoint("\u2764\uFE0F") == "2764-fe0f"

    def test_zwj_sequence(self):
        # 👨‍🍳 = U+1F468 U+200D U+1F373
        assert emoji_to_codepoint("\U0001F468\u200D\U0001F373") == "1f468-200d-1f373"


class TestUrlToCacheFilename:
    def test_produces_hash_filename(self):
        name = _url_to_cache_filename("https://www.gstatic.com/emoji/test.png")
        assert name.endswith(".png")
        assert len(name) > 4  # hash + extension

    def test_unknown_extension_defaults_to_png(self):
        name = _url_to_cache_filename("https://www.gstatic.com/emoji/test.bmp")
        assert name.endswith(".png")

    def test_valid_extensions_preserved(self):
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            name = _url_to_cache_filename(f"https://www.gstatic.com/emoji/test{ext}")
            assert name.endswith(ext)


class TestImageMagic:
    def test_valid_png(self):
        data = b'\x89PNG\r\n\x1a\n' + b'\x00' * 20
        assert _is_valid_image_magic(data) is True

    def test_valid_jpeg(self):
        data = b'\xff\xd8\xff' + b'\x00' * 20
        assert _is_valid_image_magic(data) is True

    def test_valid_gif87a(self):
        data = b'GIF87a' + b'\x00' * 20
        assert _is_valid_image_magic(data) is True

    def test_valid_gif89a(self):
        data = b'GIF89a' + b'\x00' * 20
        assert _is_valid_image_magic(data) is True

    def test_valid_webp(self):
        data = b'RIFF\x00\x00\x00\x00WEBP' + b'\x00' * 20
        assert _is_valid_image_magic(data) is True

    def test_invalid_magic(self):
        data = b'BADMAGIC' + b'\x00' * 20
        assert _is_valid_image_magic(data) is False

    def test_empty_data(self):
        assert _is_valid_image_magic(b'') is False

    def test_short_data(self):
        assert _is_valid_image_magic(b'\x89PN') is False


class TestCodepointVariants:
    def test_no_fe0f(self):
        assert _codepoint_variants("1f600") == ["1f600"]

    def test_with_fe0f(self):
        variants = _codepoint_variants("2764-fe0f")
        assert variants == ["2764-fe0f", "2764"]

    def test_with_fe0f_in_zwj(self):
        variants = _codepoint_variants("1f468-200d-2764-fe0f-200d-1f468")
        assert variants[0] == "1f468-200d-2764-fe0f-200d-1f468"
        assert variants[1] == "1f468-200d-2764-200d-1f468"


class TestFindCombinationFE0FFallback:
    """Test that _find_combination handles redundant FE0F in codepoints."""

    def test_lookup_with_redundant_fe0f(self, plugin):
        """When emoji has redundant FE0F, fallback should strip it and find the combo."""
        plugin.metadata = {
            "data": {
                "1f600": {
                    "combinations": {
                        "1f63a": [
                            {"gStaticUrl": "https://www.gstatic.com/emoji/test.png", "isLatest": True},
                        ],
                    },
                },
                "1f63a": {"combinations": {}},
            },
        }
        # Simulate 😀\uFE0F (fully-qualified) + 😺
        # emoji_to_codepoint would produce "1f600-fe0f" for the first emoji
        result = plugin._find_combination("\U0001F600\uFE0F", "\U0001F63A")
        assert result == "https://www.gstatic.com/emoji/test.png"

    def test_lookup_without_fe0f_still_works(self, plugin):
        """Normal emojis without FE0F should continue to work."""
        plugin.metadata = MOCK_METADATA
        result = plugin._find_combination("\U0001F600", "\U0001F63A")
        assert result == "https://www.gstatic.com/emoji/1f600_1f63a.png"

    def test_lookup_with_required_fe0f(self, plugin):
        """Emojis that require FE0F (text-presentation) should work with fe0f in metadata key."""
        plugin.metadata = {
            "data": {
                "2764-fe0f": {
                    "combinations": {
                        "1f525": [
                            {"gStaticUrl": "https://www.gstatic.com/emoji/heart_fire.png", "isLatest": True},
                        ],
                    },
                },
                "1f525": {"combinations": {}},
            },
        }
        # ❤️ + 🔥
        result = plugin._find_combination("\u2764\uFE0F", "\U0001F525")
        assert result == "https://www.gstatic.com/emoji/heart_fire.png"


# ============================================================
# Tests: Metadata Lock
# ============================================================

class TestMetadataLock:
    @pytest.mark.asyncio
    async def test_metadata_lock_exists(self, plugin):
        """Plugin should have a metadata lock for concurrency protection."""
        assert hasattr(plugin, '_metadata_lock')
        assert isinstance(plugin._metadata_lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_concurrent_load_metadata_serialized(self, plugin):
        """Concurrent _load_metadata calls should be serialized by the lock."""
        call_order = []

        async def mock_download():
            call_order.append("download_start")
            await asyncio.sleep(0.05)
            call_order.append("download_end")

        plugin._download_metadata = mock_download
        plugin._cache_file = plugin._data_dir / "nonexistent.json"

        await asyncio.gather(
            plugin._load_metadata(),
            plugin._load_metadata(),
        )

        # With the lock, downloads should be serialized (start/end/start/end)
        assert call_order == [
            "download_start", "download_end",
            "download_start", "download_end",
        ]

"""
Emoji Kitchen Plugin - Unit Tests

测试核心逻辑：emoji 解析、codepoint 转换、组合查找、正则匹配、缓存逻辑。
不依赖 AstrBot 框架，可独立运行 `python -m pytest test_main.py -v`。
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ============================================================
# Mock astrbot & aiohttp — 使用 patch.dict 保证安全恢复
# ============================================================


class _FakeStar:
    def __init__(self, context):
        pass


class _FakeContext:
    pass


# get_data_dir 返回的路径会在 fixture 中被覆盖（使用跨平台临时目录）
_fake_data_dir = Path(tempfile.gettempdir()) / "_emoji_kitchen_unused"

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

# patch.dict 安全地注入 mock，模块导入后自动恢复 sys.modules
with patch.dict("sys.modules", _MOCKS):
    from main import (
        emoji_to_codepoint,
        _url_to_cache_filename,
        _is_allowed_image_url,
        EMOJI_ITER_PATTERN,
        TWO_EMOJI_MSG_PATTERN,
        EmojiKitchenPlugin,
    )
    import main as _main_module


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def plugin(tmp_path):
    """每个测试独立的 plugin 实例，使用 pytest 管理的临时目录"""
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    p = EmojiKitchenPlugin(_FakeContext())
    p._data_dir = tmp_path
    p._cache_file = tmp_path / "metadata.json"
    p._img_dir = img_dir
    p.metadata = None
    return p


@pytest.fixture
def plugin_with_meta(plugin):
    """带 mock metadata 的 plugin 实例"""
    plugin.metadata = MOCK_METADATA
    return plugin


# ============================================================
# Test: emoji_to_codepoint
# ============================================================
class TestEmojiToCodepoint:

    def test_simple_emoji(self):
        assert emoji_to_codepoint("😀") == "1f600"

    def test_cat_emoji(self):
        assert emoji_to_codepoint("😺") == "1f63a"

    def test_heart_with_fe0f(self):
        assert emoji_to_codepoint("❤️") == "2764-fe0f"

    def test_zwj_sequence(self):
        assert emoji_to_codepoint("👨‍🍳") == "1f468-200d-1f373"

    def test_flag_emoji(self):
        assert emoji_to_codepoint("🇺🇸") == "1f1fa-1f1f8"

    def test_skin_tone_emoji(self):
        assert emoji_to_codepoint("👋🏽") == "1f44b-1f3fd"

    def test_number_sign_emoji(self):
        assert emoji_to_codepoint("#️⃣") == "23-fe0f-20e3"

    def test_fire(self):
        assert emoji_to_codepoint("🔥") == "1f525"

    def test_skull(self):
        assert emoji_to_codepoint("💀") == "1f480"

    def test_codepoint_lowercase(self):
        assert emoji_to_codepoint("😀") == emoji_to_codepoint("😀").lower()

    def test_multichar_emoji_split(self):
        assert len(emoji_to_codepoint("👨‍👩‍👧‍👦").split("-")) > 1


# ============================================================
# Test: _url_to_cache_filename
# ============================================================
class TestUrlToCacheFilename:

    def test_different_urls_different_names(self):
        f1 = _url_to_cache_filename("https://example.com/a.png")
        f2 = _url_to_cache_filename("https://example.com/b.png")
        assert f1 != f2

    def test_preserves_extension(self):
        assert _url_to_cache_filename("https://example.com/img.webp").endswith(".webp")

    def test_default_extension(self):
        assert _url_to_cache_filename("https://example.com/no-extension").endswith(".png")

    def test_deterministic(self):
        url = "https://example.com/test.png"
        assert _url_to_cache_filename(url) == _url_to_cache_filename(url)


# ============================================================
# Test: Regex patterns
# ============================================================
class TestRegexPatterns:

    def test_two_emoji_adjacent(self):
        m = TWO_EMOJI_MSG_PATTERN.match("😀😺")
        assert m is not None
        assert m.group(1) == "😀"
        assert m.group(2) == "😺"

    def test_two_emoji_with_space(self):
        assert TWO_EMOJI_MSG_PATTERN.match("😀 😺") is not None

    def test_two_emoji_with_padding(self):
        assert TWO_EMOJI_MSG_PATTERN.match("  😀😺  ") is not None

    def test_reject_text_with_emoji(self):
        assert TWO_EMOJI_MSG_PATTERN.match("hello 😀😺") is None

    def test_reject_single_emoji(self):
        assert TWO_EMOJI_MSG_PATTERN.match("😀") is None

    def test_reject_three_emoji(self):
        assert TWO_EMOJI_MSG_PATTERN.match("😀😺🎉") is None

    def test_reject_empty_string(self):
        assert TWO_EMOJI_MSG_PATTERN.match("") is None

    def test_reject_plain_text(self):
        assert TWO_EMOJI_MSG_PATTERN.match("hello world") is None

    def test_reject_numbers(self):
        assert TWO_EMOJI_MSG_PATTERN.match("12") is None

    def test_two_zwj_emoji(self):
        m = TWO_EMOJI_MSG_PATTERN.match("👨‍🍳👩‍🎤")
        assert m is not None
        assert m.group(1) == "👨‍🍳"
        assert m.group(2) == "👩‍🎤"

    def test_two_same_emoji(self):
        assert TWO_EMOJI_MSG_PATTERN.match("🔥🔥") is not None

    def test_skin_tone_emoji_as_two(self):
        """带肤色修饰符的 emoji 必须匹配为两个完整 emoji"""
        emojis = [m.group(0) for m in EMOJI_ITER_PATTERN.finditer("👋🏽🤚🏿")]
        assert len(emojis) == 2
        assert emojis[0] == "👋🏽"
        assert emojis[1] == "🤚🏿"

    def test_flag_emoji_as_one(self):
        """旗帜 emoji 应被识别为一个 emoji"""
        emojis = [m.group(0) for m in EMOJI_ITER_PATTERN.finditer("🇺🇸")]
        assert len(emojis) == 1
        assert emojis[0] == "🇺🇸"

    def test_two_flags(self):
        """两个旗帜 emoji 应匹配为两个"""
        m = TWO_EMOJI_MSG_PATTERN.match("🇺🇸🇯🇵")
        assert m is not None
        assert m.group(1) == "🇺🇸"
        assert m.group(2) == "🇯🇵"

    def test_keycap_emoji(self):
        """keycap emoji 应被识别为一个"""
        emojis = [m.group(0) for m in EMOJI_ITER_PATTERN.finditer("1️⃣")]
        assert len(emojis) == 1

    def test_iter_extracts_all(self):
        emojis = [m.group(0) for m in EMOJI_ITER_PATTERN.finditer("hello 😀 world 😺 bye")]
        assert "😀" in emojis
        assert "😺" in emojis

    def test_iter_adjacent_emoji(self):
        assert len([m.group(0) for m in EMOJI_ITER_PATTERN.finditer("😀😺🎉")]) == 3

    def test_iter_no_emoji(self):
        assert len([m.group(0) for m in EMOJI_ITER_PATTERN.finditer("hello world 123")]) == 0


# ============================================================
# Mock metadata
# ============================================================
MOCK_METADATA = {
    "knownSupportedEmoji": ["1f600", "1f63a", "1f525"],
    "data": {
        "1f600": {
            "combinations": {
                "1f63a": [
                    {"gStaticUrl": "https://www.gstatic.com/emoji/1f600_1f63a.png", "isLatest": True},
                    {"gStaticUrl": "https://www.gstatic.com/emoji/1f600_1f63a_old.png", "isLatest": False},
                ],
            },
        },
        "1f63a": {
            "combinations": {
                "1f525": [
                    {"gStaticUrl": "https://www.gstatic.com/emoji/1f63a_1f525.png", "isLatest": True},
                ],
            },
        },
        "1f525": {"combinations": {}},
    },
}


# ============================================================
# Test: Combination lookup
# ============================================================
class TestLookup:

    def test_direct_lookup(self, plugin_with_meta):
        assert plugin_with_meta._find_combination("😀", "😺") == "https://www.gstatic.com/emoji/1f600_1f63a.png"

    def test_reverse_lookup(self, plugin_with_meta):
        assert plugin_with_meta._find_combination("😺", "😀") == "https://www.gstatic.com/emoji/1f600_1f63a.png"

    def test_latest_version_preferred(self, plugin_with_meta):
        assert "old" not in plugin_with_meta._find_combination("😀", "😺")

    def test_unsupported_combination(self, plugin_with_meta):
        assert plugin_with_meta._find_combination("😀", "🔥") is None

    def test_no_metadata(self, plugin):
        assert plugin._find_combination("😀", "😺") is None

    def test_empty_metadata(self, plugin):
        plugin.metadata = {"data": {}}
        assert plugin._find_combination("😀", "😺") is None

    def test_fallback_no_islatest(self, plugin):
        plugin.metadata = {"data": {"1f600": {"combinations": {"1f63a": [
            {"gStaticUrl": "https://example.com/first.png", "isLatest": False},
            {"gStaticUrl": "https://example.com/second.png", "isLatest": False},
        ]}}}}
        assert plugin._find_combination("😀", "😺") == "https://example.com/first.png"

    def test_empty_combo_list(self, plugin):
        plugin.metadata = {"data": {"1f600": {"combinations": {"1f63a": []}}}}
        assert plugin._find_combination("😀", "😺") is None

    def test_same_emoji_combination(self, plugin):
        plugin.metadata = {"data": {"1f525": {"combinations": {"1f525": [
            {"gStaticUrl": "https://example.com/fire.png", "isLatest": True}
        ]}}}}
        assert plugin._find_combination("🔥", "🔥") == "https://example.com/fire.png"

    def test_islatest_without_url_falls_through(self, plugin):
        """isLatest=True 但缺少 gStaticUrl 时，应回退到其他版本"""
        plugin.metadata = {"data": {"1f600": {"combinations": {"1f63a": [
            {"isLatest": True},
            {"gStaticUrl": "https://example.com/fallback.png", "isLatest": False},
        ]}}}}
        assert plugin._find_combination("😀", "😺") == "https://example.com/fallback.png"

    def test_lookup_missing_combinations_key(self, plugin):
        plugin.metadata = {"data": {"1f600": {"alt": "test"}}}
        assert plugin._find_combination("😀", "😺") is None


# ============================================================
# Test: Cache logic (async)
# ============================================================
class TestCacheLogic:

    @pytest.mark.asyncio
    async def test_fresh_cache_skips_download(self, plugin):
        plugin._cache_file.write_text(json.dumps(MOCK_METADATA), encoding="utf-8")
        plugin._download_metadata = AsyncMock()

        await plugin._load_metadata()

        plugin._download_metadata.assert_not_called()
        assert plugin.metadata is not None

    @pytest.mark.asyncio
    async def test_expired_cache_triggers_download(self, plugin):
        plugin._cache_file.write_text(json.dumps(MOCK_METADATA), encoding="utf-8")
        old_time = plugin._cache_file.stat().st_mtime - (8 * 24 * 3600)
        os.utime(str(plugin._cache_file), (old_time, old_time))
        plugin._download_metadata = AsyncMock()

        await plugin._load_metadata()

        plugin._download_metadata.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_cache_triggers_download(self, plugin):
        if plugin._cache_file.exists():
            plugin._cache_file.unlink()
        plugin._download_metadata = AsyncMock()

        await plugin._load_metadata()

        plugin._download_metadata.assert_called_once()

    @pytest.mark.asyncio
    async def test_corrupted_cache_triggers_redownload(self, plugin):
        """损坏的缓存应触发重新下载"""
        plugin._cache_file.write_text("NOT VALID JSON {{{", encoding="utf-8")
        plugin._download_metadata = AsyncMock()

        await plugin._load_metadata()

        plugin._download_metadata.assert_called_once()


# ============================================================
# Test: _is_allowed_image_url (SSRF protection)
# ============================================================
class TestIsAllowedImageUrl:

    def test_allowed_www_gstatic(self):
        assert _is_allowed_image_url("https://www.gstatic.com/emoji/foo.png") is True

    def test_allowed_gstatic_subdomain(self):
        assert _is_allowed_image_url("https://fonts.gstatic.com/emoji/foo.png") is True

    def test_allowed_arbitrary_gstatic_subdomain(self):
        assert _is_allowed_image_url("https://emoji.gstatic.com/foo.png") is True

    def test_rejected_http_scheme(self):
        assert _is_allowed_image_url("http://www.gstatic.com/emoji/foo.png") is False

    def test_rejected_unknown_domain(self):
        assert _is_allowed_image_url("https://evil.com/foo.png") is False

    def test_rejected_internal_ip(self):
        assert _is_allowed_image_url("https://192.168.1.1/foo.png") is False

    def test_rejected_localhost(self):
        assert _is_allowed_image_url("https://localhost/foo.png") is False

    def test_rejected_gstatic_lookalike(self):
        """域名伪装为 gstatic.com 的子串不应通过"""
        assert _is_allowed_image_url("https://evil-gstatic.com/foo.png") is False

    def test_rejected_empty_string(self):
        assert _is_allowed_image_url("") is False

    def test_rejected_ftp_scheme(self):
        assert _is_allowed_image_url("ftp://www.gstatic.com/foo.png") is False


# ============================================================
# Test: _url_to_cache_filename — query-string safety
# ============================================================
class TestUrlToCacheFilenameQueryString:

    def test_query_string_not_in_extension(self):
        name = _url_to_cache_filename("https://www.gstatic.com/a.png?x=1")
        assert "?" not in name
        assert name.endswith(".png")

    def test_fragment_not_in_extension(self):
        name = _url_to_cache_filename("https://www.gstatic.com/a.webp#section")
        assert "#" not in name
        assert name.endswith(".webp")


# ============================================================
# Test: mix_command emoji count validation
# ============================================================
class TestMixCommandEmojiCount:

    @pytest.mark.asyncio
    async def test_mix_exactly_two_emoji_proceeds(self, plugin_with_meta):
        """两个 emoji 时命令应尝试合成（不因数量返回错误）"""
        mock_event = MagicMock()
        mock_event.message_str = "😀😺"
        mock_event.plain_result = lambda msg: msg
        mock_event.chain_result = lambda chain: chain

        plugin_with_meta._download_image = AsyncMock(return_value=None)
        results = []
        async for r in plugin_with_meta.mix_command(mock_event):
            results.append(r)

        # Should not get "恰好两个" error; may get download-failure message instead
        assert all("恰好两个" not in str(r) for r in results)

    @pytest.mark.asyncio
    async def test_mix_three_emoji_returns_error(self, plugin_with_meta):
        """三个 emoji 时应返回提示，不静默截断"""
        mock_event = MagicMock()
        mock_event.message_str = "😀😺🎉"
        mock_event.plain_result = lambda msg: msg

        results = []
        async for r in plugin_with_meta.mix_command(mock_event):
            results.append(r)

        assert len(results) == 1
        assert "恰好两个" in results[0]

    @pytest.mark.asyncio
    async def test_mix_one_emoji_returns_error(self, plugin_with_meta):
        """一个 emoji 时同样返回提示"""
        mock_event = MagicMock()
        mock_event.message_str = "😀"
        mock_event.plain_result = lambda msg: msg

        results = []
        async for r in plugin_with_meta.mix_command(mock_event):
            results.append(r)

        assert len(results) == 1
        assert "恰好两个" in results[0]


# ============================================================
# Helpers for async download tests
# ============================================================
def _make_resp_mock(content_type="image/png", chunks=(b"x" * 200,), raise_on_raise_for_status=False):
    resp = MagicMock()
    resp.content_type = content_type
    if raise_on_raise_for_status:
        resp.raise_for_status = MagicMock(side_effect=Exception("HTTP error"))
    else:
        resp.raise_for_status = MagicMock()

    async def _iter_chunked(size):
        for chunk in chunks:
            yield chunk

    resp.content = MagicMock()
    resp.content.iter_chunked = _iter_chunked
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=None)
    return resp


def _make_session_mock(resp):
    session = MagicMock()
    session.get = MagicMock(return_value=resp)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


# ============================================================
# Test: _download_image async behaviour
# ============================================================
class TestDownloadImage:

    @pytest.mark.asyncio
    async def test_ssrf_blocked_url_returns_none(self, plugin):
        """非 gstatic.com URL 应被拒绝，不发出任何 HTTP 请求"""
        result = await plugin._download_image("https://evil.com/foo.png")
        assert result is None

    @pytest.mark.asyncio
    async def test_ssrf_http_scheme_blocked(self, plugin):
        result = await plugin._download_image("http://www.gstatic.com/emoji/foo.png")
        assert result is None

    @pytest.mark.asyncio
    async def test_cached_file_returned_without_download(self, plugin):
        """已缓存文件直接返回，不触发下载"""
        url = "https://www.gstatic.com/emoji/1f600_1f63a.png"
        cached = plugin._img_dir / _url_to_cache_filename(url)
        cached.write_bytes(b"fake")

        result = await plugin._download_image(url)
        assert result == str(cached)

    @pytest.mark.asyncio
    async def test_successful_download_creates_file(self, plugin):
        url = "https://www.gstatic.com/emoji/1f600_1f63a.png"
        resp = _make_resp_mock(chunks=(b"x" * 200,))
        session = _make_session_mock(resp)

        with patch.object(_main_module.aiohttp, "ClientSession", return_value=session):
            result = await plugin._download_image(url)

        assert result is not None
        assert Path(result).exists()

    @pytest.mark.asyncio
    async def test_too_large_response_returns_none(self, plugin):
        """超过 MAX_IMAGE_BYTES 的响应应被拒绝"""
        url = "https://www.gstatic.com/emoji/big.png"
        large_chunk = b"x" * (_main_module.MAX_IMAGE_BYTES + 1)
        resp = _make_resp_mock(chunks=(large_chunk,))
        session = _make_session_mock(resp)

        with patch.object(_main_module.aiohttp, "ClientSession", return_value=session):
            result = await plugin._download_image(url)

        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_content_type_returns_none(self, plugin):
        """非图片 Content-Type 应被拒绝"""
        url = "https://www.gstatic.com/emoji/bad.png"
        resp = _make_resp_mock(content_type="text/html", chunks=(b"x" * 200,))
        session = _make_session_mock(resp)

        with patch.object(_main_module.aiohttp, "ClientSession", return_value=session):
            result = await plugin._download_image(url)

        assert result is None

    @pytest.mark.asyncio
    async def test_lock_cleaned_up_after_success(self, plugin):
        """下载成功后锁映射中不应残留条目"""
        url = "https://www.gstatic.com/emoji/1f600_1f63a.png"
        resp = _make_resp_mock(chunks=(b"x" * 200,))
        session = _make_session_mock(resp)

        with patch.object(_main_module.aiohttp, "ClientSession", return_value=session):
            await plugin._download_image(url)

        filename = _url_to_cache_filename(url)
        assert filename not in plugin._download_locks

    @pytest.mark.asyncio
    async def test_lock_cleaned_up_after_all_mirrors_fail(self, plugin):
        """所有镜像失败后锁映射中不应残留条目"""
        url = "https://www.gstatic.com/emoji/fail.png"
        resp = _make_resp_mock(raise_on_raise_for_status=True)
        session = _make_session_mock(resp)

        with patch.object(_main_module.aiohttp, "ClientSession", return_value=session):
            result = await plugin._download_image(url)

        assert result is None
        filename = _url_to_cache_filename(url)
        assert filename not in plugin._download_locks

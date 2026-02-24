"""
Emoji Kitchen Plugin - Unit Tests

测试核心逻辑：emoji 解析、codepoint 转换、组合查找、正则匹配、缓存逻辑。
不依赖 AstrBot 框架，可独立运行 `python -m pytest test_main.py -v`。
"""

import json
import os
import sys
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


# get_data_dir 返回的路径会在 fixture 中被覆盖
_fake_data_dir = Path("/tmp/_emoji_kitchen_unused")

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
        EMOJI_ITER_PATTERN,
        TWO_EMOJI_MSG_PATTERN,
        EmojiKitchenPlugin,
    )


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

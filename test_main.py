"""
Emoji Kitchen Plugin - Unit Tests

测试核心逻辑：emoji 解析、codepoint 转换、组合查找、正则匹配、缓存逻辑。
不依赖 AstrBot 框架，可独立运行 `python -m pytest test_main.py -v`。
"""

import json
import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ============================================================
# Mock astrbot & aiohttp before importing main
# ============================================================
import sys


class _FakeStar:
    def __init__(self, context):
        pass


class _FakeContext:
    pass


# 每次测试运行使用独立目录，避免串扰
_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="emoji_kitchen_test_"))


class _FakeStarTools:
    @staticmethod
    def get_data_dir(name: str) -> Path:
        d = _TEST_DATA_DIR / name
        d.mkdir(parents=True, exist_ok=True)
        return d


_star_module = MagicMock()
_star_module.Star = _FakeStar
_star_module.Context = _FakeContext
_star_module.StarTools = _FakeStarTools
_star_module.register = lambda *a, **kw: lambda cls: cls

_filter_mock = MagicMock()
_filter_mock.command = lambda *a, **kw: lambda fn: fn
_filter_mock.event_message_type = lambda *a, **kw: lambda fn: fn

_event_module = MagicMock()
_event_module.filter = _filter_mock

_filter_module = MagicMock()
_filter_module.EventMessageType = MagicMock()

_original_modules: dict[str, object] = {}
_mocked_keys = [
    "aiohttp", "astrbot", "astrbot.api", "astrbot.api.event",
    "astrbot.api.event.filter", "astrbot.api.star",
    "astrbot.api.message_components",
]


def _install_mocks():
    mocks = {
        "aiohttp": MagicMock(),
        "astrbot": MagicMock(),
        "astrbot.api": MagicMock(),
        "astrbot.api.event": _event_module,
        "astrbot.api.event.filter": _filter_module,
        "astrbot.api.star": _star_module,
        "astrbot.api.message_components": MagicMock(),
    }
    for key in _mocked_keys:
        _original_modules[key] = sys.modules.get(key)
    sys.modules.update(mocks)


def _uninstall_mocks():
    for key in _mocked_keys:
        orig = _original_modules.get(key)
        if orig is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = orig


_install_mocks()

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
def _make_plugin(metadata=None):
    """构造可供测试的 plugin 实例（通过 __init__ 正常初始化）"""
    plugin = EmojiKitchenPlugin(_FakeContext())
    plugin.metadata = metadata
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
        f = _url_to_cache_filename("https://example.com/img.webp")
        assert f.endswith(".webp")

    def test_default_extension(self):
        f = _url_to_cache_filename("https://example.com/no-extension")
        assert f.endswith(".png")

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
        m = TWO_EMOJI_MSG_PATTERN.match("😀 😺")
        assert m is not None

    def test_two_emoji_with_padding(self):
        m = TWO_EMOJI_MSG_PATTERN.match("  😀😺  ")
        assert m is not None

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
        m = TWO_EMOJI_MSG_PATTERN.match("🔥🔥")
        assert m is not None

    def test_skin_tone_emoji_as_two(self):
        """带肤色修饰符的 emoji 必须匹配为两个完整 emoji"""
        emojis = [m.group(0) for m in EMOJI_ITER_PATTERN.finditer("👋🏽🤚🏿")]
        assert len(emojis) == 2
        assert emojis[0] == "👋🏽"
        assert emojis[1] == "🤚🏿"

    def test_flag_emoji_as_one(self):
        """旗帜 emoji（两个 regional indicator）应被识别为一个 emoji"""
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
        emojis = [m.group(0) for m in EMOJI_ITER_PATTERN.finditer("😀😺🎉")]
        assert len(emojis) == 3

    def test_iter_no_emoji(self):
        emojis = [m.group(0) for m in EMOJI_ITER_PATTERN.finditer("hello world 123")]
        assert len(emojis) == 0


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

    def test_direct_lookup(self):
        plugin = _make_plugin(MOCK_METADATA)
        assert plugin._find_combination("😀", "😺") == "https://www.gstatic.com/emoji/1f600_1f63a.png"

    def test_reverse_lookup(self):
        plugin = _make_plugin(MOCK_METADATA)
        assert plugin._find_combination("😺", "😀") == "https://www.gstatic.com/emoji/1f600_1f63a.png"

    def test_latest_version_preferred(self):
        plugin = _make_plugin(MOCK_METADATA)
        assert "old" not in plugin._find_combination("😀", "😺")

    def test_unsupported_combination(self):
        assert _make_plugin(MOCK_METADATA)._find_combination("😀", "🔥") is None

    def test_no_metadata(self):
        assert _make_plugin(None)._find_combination("😀", "😺") is None

    def test_empty_metadata(self):
        assert _make_plugin({"data": {}})._find_combination("😀", "😺") is None

    def test_fallback_no_islatest(self):
        metadata = {"data": {"1f600": {"combinations": {"1f63a": [
            {"gStaticUrl": "https://example.com/first.png", "isLatest": False},
            {"gStaticUrl": "https://example.com/second.png", "isLatest": False},
        ]}}}}
        assert _make_plugin(metadata)._find_combination("😀", "😺") == "https://example.com/first.png"

    def test_empty_combo_list(self):
        metadata = {"data": {"1f600": {"combinations": {"1f63a": []}}}}
        assert _make_plugin(metadata)._find_combination("😀", "😺") is None

    def test_same_emoji_combination(self):
        metadata = {"data": {"1f525": {"combinations": {"1f525": [
            {"gStaticUrl": "https://example.com/fire_fire.png", "isLatest": True}
        ]}}}}
        assert _make_plugin(metadata)._find_combination("🔥", "🔥") == "https://example.com/fire_fire.png"

    def test_islatest_without_url_falls_through(self):
        """isLatest=True 但缺少 gStaticUrl 时，应回退到其他版本"""
        metadata = {"data": {"1f600": {"combinations": {"1f63a": [
            {"isLatest": True},
            {"gStaticUrl": "https://example.com/fallback.png", "isLatest": False},
        ]}}}}
        assert _make_plugin(metadata)._find_combination("😀", "😺") == "https://example.com/fallback.png"

    def test_lookup_missing_combinations_key(self):
        assert _make_plugin({"data": {"1f600": {"alt": "test"}}})._find_combination("😀", "😺") is None


# ============================================================
# Test: Cache logic (async)
# ============================================================
class TestCacheLogic:

    @pytest.mark.asyncio
    async def test_fresh_cache_skips_download(self):
        plugin = _make_plugin()
        plugin._cache_file.write_text(json.dumps(MOCK_METADATA), encoding="utf-8")
        plugin._download_metadata = AsyncMock()

        await plugin._load_metadata()

        plugin._download_metadata.assert_not_called()
        assert plugin.metadata is not None

    @pytest.mark.asyncio
    async def test_expired_cache_triggers_download(self):
        plugin = _make_plugin()
        plugin._cache_file.write_text(json.dumps(MOCK_METADATA), encoding="utf-8")
        old_time = plugin._cache_file.stat().st_mtime - (8 * 24 * 3600)
        os.utime(str(plugin._cache_file), (old_time, old_time))
        plugin._download_metadata = AsyncMock()

        await plugin._load_metadata()

        plugin._download_metadata.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_cache_triggers_download(self):
        plugin = _make_plugin()
        if plugin._cache_file.exists():
            plugin._cache_file.unlink()
        plugin._download_metadata = AsyncMock()

        await plugin._load_metadata()

        plugin._download_metadata.assert_called_once()

    @pytest.mark.asyncio
    async def test_corrupted_cache_triggers_redownload(self):
        """损坏的缓存应触发重新下载"""
        plugin = _make_plugin()
        plugin._cache_file.write_text("NOT VALID JSON {{{", encoding="utf-8")
        plugin._download_metadata = AsyncMock()

        await plugin._load_metadata()

        # 应调用 _download_metadata 进行重新下载
        plugin._download_metadata.assert_called_once()


# ============================================================
# Cleanup
# ============================================================
def teardown_module():
    _uninstall_mocks()
    # 清理测试目录
    import shutil
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)

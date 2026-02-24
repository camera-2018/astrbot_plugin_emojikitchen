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
import pytest_asyncio  # noqa: F401 — ensure pytest-asyncio is available
import regex

# ============================================================
# Mock astrbot & aiohttp before importing main
# ============================================================
import sys


class _FakeStar:
    def __init__(self, context):
        pass


class _FakeContext:
    pass


class _FakeStarTools:
    @staticmethod
    def get_data_dir(name: str) -> Path:
        """返回一个临时目录用于测试"""
        d = Path(tempfile.gettempdir()) / "emoji_kitchen_test"
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
_event_module.EventMessageType = MagicMock()

_filter_module = MagicMock()
_filter_module.EventMessageType = MagicMock()

_original_modules = {}
_mocked_keys = [
    "aiohttp", "astrbot", "astrbot.api", "astrbot.api.event",
    "astrbot.api.event.filter", "astrbot.api.star",
    "astrbot.api.message_components",
]


def _install_mocks():
    """安装 mock 到 sys.modules 并保存原始引用"""
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
    """恢复 sys.modules 原始状态"""
    for key in _mocked_keys:
        orig = _original_modules.get(key)
        if orig is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = orig


_install_mocks()

# Now import from main
from main import (
    emoji_to_codepoint,
    EMOJI_ITER_PATTERN,
    TWO_EMOJI_MSG_PATTERN,
    EmojiKitchenPlugin,
)


# ============================================================
# Fixture: clean plugin instance
# ============================================================
def _make_plugin(metadata=None):
    """构造一个可供测试的 plugin 实例（通过 __init__ 正常初始化）"""
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

    def test_simple_smiley(self):
        assert emoji_to_codepoint("🙂") == "1f642"

    def test_fire(self):
        assert emoji_to_codepoint("🔥") == "1f525"

    def test_skull(self):
        assert emoji_to_codepoint("💀") == "1f480"

    def test_codepoint_lowercase(self):
        cp = emoji_to_codepoint("😀")
        assert cp == cp.lower()

    def test_multichar_emoji_split(self):
        cp = emoji_to_codepoint("👨‍👩‍👧‍👦")
        assert len(cp.split("-")) > 1


# ============================================================
# Test: Regex patterns
# ============================================================
class TestRegexPatterns:

    # --- TWO_EMOJI_MSG_PATTERN ---

    def test_two_emoji_adjacent(self):
        m = TWO_EMOJI_MSG_PATTERN.match("😀😺")
        assert m is not None
        assert m.group(1) == "😀"
        assert m.group(2) == "😺"

    def test_two_emoji_with_space(self):
        m = TWO_EMOJI_MSG_PATTERN.match("😀 😺")
        assert m is not None
        assert m.group(1) == "😀"
        assert m.group(2) == "😺"

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
        assert m.group(1) == "🔥"
        assert m.group(2) == "🔥"

    def test_two_emoji_with_skin_tones(self):
        """带肤色修饰符的 emoji 必须匹配为两个"""
        emojis = [m.group(0) for m in EMOJI_ITER_PATTERN.finditer("👋🏽🤚🏿")]
        # 应该提取出两个完整的 emoji（含肤色修饰符），不应拆成 4 段
        assert len(emojis) == 2
        assert emojis[0] == "👋🏽"
        assert emojis[1] == "🤚🏿"

    # --- EMOJI_ITER_PATTERN ---

    def test_iter_extracts_all(self):
        text = "hello 😀 world 😺 bye"
        emojis = [m.group(0) for m in EMOJI_ITER_PATTERN.finditer(text)]
        assert "😀" in emojis
        assert "😺" in emojis

    def test_iter_adjacent_emoji(self):
        emojis = [m.group(0) for m in EMOJI_ITER_PATTERN.finditer("😀😺🎉")]
        assert len(emojis) == 3

    def test_iter_no_emoji(self):
        emojis = [m.group(0) for m in EMOJI_ITER_PATTERN.finditer("hello world 123")]
        assert len(emojis) == 0

    def test_variation_selector_emoji(self):
        """带 variation selector (FE0F) 的 emoji"""
        cp = emoji_to_codepoint("❤️")
        assert "fe0f" in cp


# ============================================================
# Mock metadata
# ============================================================
MOCK_METADATA = {
    "knownSupportedEmoji": ["1f600", "1f63a", "1f525"],
    "data": {
        "1f600": {
            "combinations": {
                "1f63a": [
                    {
                        "gStaticUrl": "https://www.gstatic.com/emoji/1f600_1f63a.png",
                        "isLatest": True,
                    },
                    {
                        "gStaticUrl": "https://www.gstatic.com/emoji/1f600_1f63a_old.png",
                        "isLatest": False,
                    },
                ],
            },
        },
        "1f63a": {
            "combinations": {
                "1f525": [
                    {
                        "gStaticUrl": "https://www.gstatic.com/emoji/1f63a_1f525.png",
                        "isLatest": True,
                    },
                ],
            },
        },
        "1f525": {
            "combinations": {},
        },
    },
}


# ============================================================
# Test: Combination lookup
# ============================================================
class TestLookup:

    def test_direct_lookup(self):
        plugin = _make_plugin(MOCK_METADATA)
        url = plugin._find_combination("😀", "😺")
        assert url == "https://www.gstatic.com/emoji/1f600_1f63a.png"

    def test_reverse_lookup(self):
        plugin = _make_plugin(MOCK_METADATA)
        url = plugin._find_combination("😺", "😀")
        assert url == "https://www.gstatic.com/emoji/1f600_1f63a.png"

    def test_latest_version_preferred(self):
        plugin = _make_plugin(MOCK_METADATA)
        url = plugin._find_combination("😀", "😺")
        assert "old" not in url

    def test_unsupported_combination(self):
        plugin = _make_plugin(MOCK_METADATA)
        url = plugin._find_combination("😀", "🔥")
        assert url is None

    def test_no_metadata(self):
        plugin = _make_plugin(None)
        url = plugin._find_combination("😀", "😺")
        assert url is None

    def test_empty_metadata(self):
        plugin = _make_plugin({"data": {}})
        url = plugin._find_combination("😀", "😺")
        assert url is None

    def test_fallback_no_islatest(self):
        """所有版本都没有 isLatest=True 时，取第一个有 URL 的"""
        metadata = {
            "data": {
                "1f600": {
                    "combinations": {
                        "1f63a": [
                            {"gStaticUrl": "https://example.com/first.png", "isLatest": False},
                            {"gStaticUrl": "https://example.com/second.png", "isLatest": False},
                        ]
                    }
                }
            }
        }
        plugin = _make_plugin(metadata)
        url = plugin._find_combination("😀", "😺")
        assert url == "https://example.com/first.png"

    def test_empty_combo_list(self):
        metadata = {"data": {"1f600": {"combinations": {"1f63a": []}}}}
        plugin = _make_plugin(metadata)
        url = plugin._find_combination("😀", "😺")
        assert url is None

    def test_same_emoji_combination(self):
        metadata = {
            "data": {
                "1f525": {
                    "combinations": {
                        "1f525": [
                            {"gStaticUrl": "https://example.com/fire_fire.png", "isLatest": True}
                        ]
                    }
                }
            }
        }
        plugin = _make_plugin(metadata)
        url = plugin._find_combination("🔥", "🔥")
        assert url == "https://example.com/fire_fire.png"

    def test_islatest_without_url_falls_through(self):
        """isLatest=True 但缺少 gStaticUrl 时，应回退到其他版本"""
        metadata = {
            "data": {
                "1f600": {
                    "combinations": {
                        "1f63a": [
                            {"isLatest": True},  # 没有 gStaticUrl
                            {"gStaticUrl": "https://example.com/fallback.png", "isLatest": False},
                        ]
                    }
                }
            }
        }
        plugin = _make_plugin(metadata)
        url = plugin._find_combination("😀", "😺")
        assert url == "https://example.com/fallback.png"

    def test_lookup_missing_combinations_key(self):
        metadata = {"data": {"1f600": {"alt": "test"}}}
        plugin = _make_plugin(metadata)
        url = plugin._find_combination("😀", "😺")
        assert url is None


# ============================================================
# Test: Cache logic (async)
# ============================================================
class TestCacheLogic:

    @pytest.mark.asyncio
    async def test_fresh_cache_skips_download(self):
        """缓存未过期时不应重新下载"""
        plugin = _make_plugin()
        plugin._cache_file.write_text(json.dumps(MOCK_METADATA), encoding="utf-8")

        plugin._download_metadata = AsyncMock()
        await plugin._load_metadata()

        plugin._download_metadata.assert_not_called()
        assert plugin.metadata is not None
        assert plugin.metadata["knownSupportedEmoji"] == ["1f600", "1f63a", "1f525"]

    @pytest.mark.asyncio
    async def test_expired_cache_triggers_download(self):
        """缓存过期时应触发下载"""
        plugin = _make_plugin()
        plugin._cache_file.write_text(json.dumps(MOCK_METADATA), encoding="utf-8")

        # 将文件修改时间设为 8 天前
        old_time = plugin._cache_file.stat().st_mtime - (8 * 24 * 3600)
        os.utime(str(plugin._cache_file), (old_time, old_time))

        plugin._download_metadata = AsyncMock()
        await plugin._load_metadata()

        plugin._download_metadata.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_cache_triggers_download(self):
        """缓存不存在时应触发下载"""
        plugin = _make_plugin()
        if plugin._cache_file.exists():
            plugin._cache_file.unlink()

        plugin._download_metadata = AsyncMock()
        await plugin._load_metadata()

        plugin._download_metadata.assert_called_once()

    @pytest.mark.asyncio
    async def test_corrupted_cache_sets_none(self):
        """损坏的缓存文件应导致 metadata 为 None"""
        plugin = _make_plugin()
        plugin._cache_file.write_text("NOT VALID JSON {{{", encoding="utf-8")

        plugin._download_metadata = AsyncMock()
        await plugin._load_metadata()

        assert plugin.metadata is None


# ============================================================
# Cleanup: restore sys.modules
# ============================================================
def teardown_module():
    _uninstall_mocks()

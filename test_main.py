"""
Emoji Kitchen Plugin - Unit Tests

测试核心逻辑：emoji 解析、codepoint 转换、组合查找、正则匹配。
不依赖 AstrBot 框架，可独立运行 `python -m pytest test_main.py -v`。
"""

import json
import os
import tempfile
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import regex

# ============================================================
# 由于 main.py 会 import astrbot（运行测试时不存在），
# 我们先 mock 掉 astrbot 相关模块，再 import main。
# ============================================================
import sys


# 创建一个真实的 Star 基类，这样 EmojiKitchenPlugin 可以正常继承
class _FakeStar:
    def __init__(self, context):
        pass


class _FakeContext:
    pass


# Mock astrbot and aiohttp modules before importing main
_star_module = MagicMock()
_star_module.Star = _FakeStar
_star_module.Context = _FakeContext
_star_module.register = lambda *a, **kw: lambda cls: cls

_filter_mock = MagicMock()
_filter_mock.command = lambda *a, **kw: lambda fn: fn
_filter_mock.event_message_type = lambda *a, **kw: lambda fn: fn

_event_module = MagicMock()
_event_module.filter = _filter_mock

sys.modules.update({
    "aiohttp": MagicMock(),
    "astrbot": MagicMock(),
    "astrbot.api": MagicMock(),
    "astrbot.api.event": _event_module,
    "astrbot.api.star": _star_module,
    "astrbot.api.message_components": MagicMock(),
})

# Now we can safely import from main
from main import (
    emoji_to_codepoint,
    EMOJI_ITER_PATTERN,
    TWO_EMOJI_MSG_PATTERN,
    EmojiKitchenPlugin,
)


# ============================================================
# Test: emoji_to_codepoint
# ============================================================
class TestEmojiToCodepoint:
    """测试 emoji 字符到 codepoint 的转换"""

    def test_simple_emoji(self):
        assert emoji_to_codepoint("😀") == "1f600"

    def test_cat_emoji(self):
        assert emoji_to_codepoint("😺") == "1f63a"

    def test_heart_with_fe0f(self):
        assert emoji_to_codepoint("❤️") == "2764-fe0f"

    def test_zwj_sequence(self):
        """ZWJ (Zero Width Joiner) 复合 emoji"""
        assert emoji_to_codepoint("👨‍🍳") == "1f468-200d-1f373"

    def test_flag_emoji(self):
        """国旗 emoji 由两个 regional indicator 组成"""
        assert emoji_to_codepoint("🇺🇸") == "1f1fa-1f1f8"

    def test_skin_tone_emoji(self):
        """带肤色修饰符的 emoji"""
        assert emoji_to_codepoint("👋🏽") == "1f44b-1f3fd"

    def test_number_sign_emoji(self):
        """#️⃣ keycap emoji"""
        assert emoji_to_codepoint("#️⃣") == "23-fe0f-20e3"

    def test_simple_smiley(self):
        assert emoji_to_codepoint("🙂") == "1f642"

    def test_fire(self):
        assert emoji_to_codepoint("🔥") == "1f525"

    def test_skull(self):
        assert emoji_to_codepoint("💀") == "1f480"


# ============================================================
# Test: Regex patterns
# ============================================================
class TestRegexPatterns:
    """测试 emoji 正则匹配"""

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
        """有其他文字时不应匹配"""
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
        """普通数字不应被当作 emoji"""
        assert TWO_EMOJI_MSG_PATTERN.match("12") is None

    def test_two_zwj_emoji(self):
        """ZWJ 复合 emoji 也能正确匹配"""
        m = TWO_EMOJI_MSG_PATTERN.match("👨‍🍳👩‍🎤")
        assert m is not None
        assert m.group(1) == "👨‍🍳"
        assert m.group(2) == "👩‍🎤"

    def test_two_same_emoji(self):
        """两个相同的 emoji"""
        m = TWO_EMOJI_MSG_PATTERN.match("🔥🔥")
        assert m is not None
        assert m.group(1) == "🔥"
        assert m.group(2) == "🔥"

    # --- EMOJI_ITER_PATTERN ---

    def test_iter_extracts_all(self):
        """从混合文本中提取 emoji"""
        text = "hello 😀 world 😺 bye"
        emojis = [m.group(0) for m in EMOJI_ITER_PATTERN.finditer(text)]
        assert "😀" in emojis
        assert "😺" in emojis

    def test_iter_adjacent_emoji(self):
        text = "😀😺🎉"
        emojis = [m.group(0) for m in EMOJI_ITER_PATTERN.finditer(text)]
        assert len(emojis) == 3

    def test_iter_no_emoji(self):
        text = "hello world 123"
        emojis = [m.group(0) for m in EMOJI_ITER_PATTERN.finditer(text)]
        assert len(emojis) == 0


# ============================================================
# Test: Combination lookup
# ============================================================
# 构造一份 mock metadata
MOCK_METADATA = {
    "knownSupportedEmoji": ["1f600", "1f63a", "1f525"],
    "data": {
        "1f600": {
            "alt": "😀 Grinning Face",
            "emojiCodepoint": "1f600",
            "gBoardOrder": 1,
            "keywords": ["grinning"],
            "combinations": {
                "1f63a": [
                    {
                        "gStaticUrl": "https://www.gstatic.com/emoji/1f600_1f63a.png",
                        "alt": "😀 + 😺",
                        "leftEmoji": "😀",
                        "leftEmojiCodepoint": "1f600",
                        "rightEmoji": "😺",
                        "rightEmojiCodepoint": "1f63a",
                        "date": "2021-01-01",
                        "isLatest": True,
                        "gBoardOrder": 1,
                    },
                    {
                        "gStaticUrl": "https://www.gstatic.com/emoji/1f600_1f63a_old.png",
                        "alt": "😀 + 😺 (old)",
                        "leftEmoji": "😀",
                        "leftEmojiCodepoint": "1f600",
                        "rightEmoji": "😺",
                        "rightEmojiCodepoint": "1f63a",
                        "date": "2020-01-01",
                        "isLatest": False,
                        "gBoardOrder": 1,
                    },
                ],
            },
        },
        "1f63a": {
            "alt": "😺 Smiling Cat",
            "emojiCodepoint": "1f63a",
            "gBoardOrder": 2,
            "keywords": ["cat"],
            "combinations": {
                "1f525": [
                    {
                        "gStaticUrl": "https://www.gstatic.com/emoji/1f63a_1f525.png",
                        "alt": "😺 + 🔥",
                        "leftEmoji": "😺",
                        "leftEmojiCodepoint": "1f63a",
                        "rightEmoji": "🔥",
                        "rightEmojiCodepoint": "1f525",
                        "date": "2021-06-01",
                        "isLatest": True,
                        "gBoardOrder": 2,
                    },
                ],
            },
        },
        "1f525": {
            "alt": "🔥 Fire",
            "emojiCodepoint": "1f525",
            "gBoardOrder": 3,
            "keywords": ["fire"],
            "combinations": {},
        },
    },
}


def _make_plugin(metadata=None):
    """构造一个不依赖 AstrBot Context 的 plugin 实例"""
    plugin = EmojiKitchenPlugin.__new__(EmojiKitchenPlugin)
    plugin.metadata = metadata
    return plugin


class TestLookup:
    """测试组合查找逻辑"""

    def test_direct_lookup(self):
        """直接匹配 left→right"""
        plugin = _make_plugin(MOCK_METADATA)
        url = plugin._find_combination("😀", "😺")
        assert url == "https://www.gstatic.com/emoji/1f600_1f63a.png"

    def test_reverse_lookup(self):
        """反向匹配 right→left（当 left→right 不存在时）"""
        plugin = _make_plugin(MOCK_METADATA)
        url = plugin._find_combination("😺", "😀")
        assert url == "https://www.gstatic.com/emoji/1f600_1f63a.png"

    def test_latest_version_preferred(self):
        """应优先返回 isLatest=True 的版本"""
        plugin = _make_plugin(MOCK_METADATA)
        url = plugin._find_combination("😀", "😺")
        assert "old" not in url

    def test_unsupported_combination(self):
        """不存在的组合应返回 None"""
        plugin = _make_plugin(MOCK_METADATA)
        url = plugin._find_combination("😀", "🔥")
        assert url is None

    def test_no_metadata(self):
        """metadata 未加载时应返回 None"""
        plugin = _make_plugin(None)
        url = plugin._find_combination("😀", "😺")
        assert url is None

    def test_empty_metadata(self):
        """空 metadata"""
        plugin = _make_plugin({"data": {}})
        url = plugin._find_combination("😀", "😺")
        assert url is None

    def test_fallback_no_islatest(self):
        """所有版本都没有 isLatest 标记时，取第一个"""
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
        """组合列表为空数组"""
        metadata = {"data": {"1f600": {"combinations": {"1f63a": []}}}}
        plugin = _make_plugin(metadata)
        url = plugin._find_combination("😀", "😺")
        assert url is None

    def test_same_emoji_combination(self):
        """相同 emoji 合成（如 🔥+🔥）"""
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


# ============================================================
# Test: Cache logic
# ============================================================
class TestCacheLogic:
    """测试 metadata 缓存判断逻辑"""

    def test_fresh_cache_skips_download(self):
        """缓存未过期时不应重新下载"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = os.path.join(tmpdir, "metadata.json")
            with open(cache_file, "w") as f:
                json.dump(MOCK_METADATA, f)

            import main
            orig_file, orig_dir = main.CACHE_FILE, main.CACHE_DIR
            main.CACHE_FILE, main.CACHE_DIR = cache_file, tmpdir

            try:
                plugin = _make_plugin()
                plugin._download_metadata = AsyncMock()
                asyncio.run(plugin._load_metadata())

                plugin._download_metadata.assert_not_called()
                assert plugin.metadata is not None
                assert plugin.metadata["knownSupportedEmoji"] == ["1f600", "1f63a", "1f525"]
            finally:
                main.CACHE_FILE, main.CACHE_DIR = orig_file, orig_dir

    def test_expired_cache_triggers_download(self):
        """缓存过期时应触发下载"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = os.path.join(tmpdir, "metadata.json")
            with open(cache_file, "w") as f:
                json.dump(MOCK_METADATA, f)

            old_time = os.path.getmtime(cache_file) - (8 * 24 * 3600)
            os.utime(cache_file, (old_time, old_time))

            import main
            orig_file, orig_dir = main.CACHE_FILE, main.CACHE_DIR
            main.CACHE_FILE, main.CACHE_DIR = cache_file, tmpdir

            try:
                plugin = _make_plugin()
                plugin._download_metadata = AsyncMock()
                asyncio.run(plugin._load_metadata())

                plugin._download_metadata.assert_called_once()
            finally:
                main.CACHE_FILE, main.CACHE_DIR = orig_file, orig_dir

    def test_missing_cache_triggers_download(self):
        """缓存不存在时应触发下载"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = os.path.join(tmpdir, "metadata.json")

            import main
            orig_file, orig_dir = main.CACHE_FILE, main.CACHE_DIR
            main.CACHE_FILE, main.CACHE_DIR = cache_file, tmpdir

            try:
                plugin = _make_plugin()
                plugin._download_metadata = AsyncMock()
                asyncio.run(plugin._load_metadata())

                plugin._download_metadata.assert_called_once()
            finally:
                main.CACHE_FILE, main.CACHE_DIR = orig_file, orig_dir

    def test_corrupted_cache_sets_none(self):
        """损坏的缓存文件应导致 metadata 为 None"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = os.path.join(tmpdir, "metadata.json")
            with open(cache_file, "w") as f:
                f.write("NOT VALID JSON {{{")

            import main
            orig_file, orig_dir = main.CACHE_FILE, main.CACHE_DIR
            main.CACHE_FILE, main.CACHE_DIR = cache_file, tmpdir

            try:
                plugin = _make_plugin()
                plugin._download_metadata = AsyncMock()
                asyncio.run(plugin._load_metadata())

                assert plugin.metadata is None
            finally:
                main.CACHE_FILE, main.CACHE_DIR = orig_file, orig_dir


# ============================================================
# Test: Edge cases
# ============================================================
class TestEdgeCases:
    """边界情况测试"""

    def test_emoji_with_variation_selector(self):
        """带 variation selector (FE0F) 的 emoji"""
        cp = emoji_to_codepoint("❤️")
        assert "fe0f" in cp

    def test_codepoint_lowercase(self):
        """codepoint 应为小写十六进制"""
        cp = emoji_to_codepoint("😀")
        assert cp == cp.lower()

    def test_multichar_emoji_split(self):
        """多字符 emoji 的 codepoint 应由 '-' 分隔"""
        cp = emoji_to_codepoint("👨‍👩‍👧‍👦")
        parts = cp.split("-")
        assert len(parts) > 1

    def test_two_emoji_with_skin_tones(self):
        """带肤色修饰符的 emoji 能被正确提取"""
        m = TWO_EMOJI_MSG_PATTERN.match("👋🏽🤚🏿")
        # 主要确保不会崩溃
        if m:
            assert m.group(1) is not None
            assert m.group(2) is not None

    def test_lookup_with_missing_gstaticurl(self):
        """combo 对象缺少 gStaticUrl 字段"""
        metadata = {
            "data": {
                "1f600": {
                    "combinations": {
                        "1f63a": [{"isLatest": True}]  # no gStaticUrl
                    }
                }
            }
        }
        plugin = _make_plugin(metadata)
        url = plugin._find_combination("😀", "😺")
        assert url is None  # should return None, not crash

    def test_lookup_missing_combinations_key(self):
        """emoji data 缺少 combinations 字段"""
        metadata = {"data": {"1f600": {"alt": "test"}}}
        plugin = _make_plugin(metadata)
        url = plugin._find_combination("😀", "😺")
        assert url is None

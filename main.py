import os
import json
import time
import regex

import aiohttp

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

METADATA_URLS = [
    "https://raw.githubusercontent.com/xsalazar/emoji-kitchen-backend/main/app/metadata.json",
    "https://ghfast.top/https://raw.githubusercontent.com/xsalazar/emoji-kitchen-backend/main/app/metadata.json",
    "https://gh-proxy.com/https://raw.githubusercontent.com/xsalazar/emoji-kitchen-backend/main/app/metadata.json",
    "https://mirror.ghproxy.com/https://raw.githubusercontent.com/xsalazar/emoji-kitchen-backend/main/app/metadata.json",
]
CACHE_DIR = os.path.join("data", "plugins", "emoji_kitchen")
CACHE_FILE = os.path.join(CACHE_DIR, "metadata.json")
CACHE_MAX_AGE = 7 * 24 * 3600  # 7 days

# Regex pattern to match a single emoji (including ZWJ sequences, skin tones, etc.)
SINGLE_EMOJI_RE = (
    r"(?:\p{Emoji_Presentation}|\p{Emoji}\uFE0F)"
    r"(?:\u200D(?:\p{Emoji_Presentation}|\p{Emoji}\uFE0F))*"
)

# Compiled patterns
EMOJI_ITER_PATTERN = regex.compile(SINGLE_EMOJI_RE)
TWO_EMOJI_MSG_PATTERN = regex.compile(
    rf"^\s*({SINGLE_EMOJI_RE})\s*({SINGLE_EMOJI_RE})\s*$"
)


def emoji_to_codepoint(emoji_char: str) -> str:
    """Convert an emoji character (possibly multi-codepoint) to dash-separated hex codepoints.

    Example: '😀' -> '1f600', '👨‍🍳' -> '1f468-200d-1f373'
    """
    return "-".join(f"{ord(c):x}" for c in emoji_char)


@register("emoji_kitchen", "camera-2018", "Emoji Kitchen - 合成两个 emoji", "1.0.0")
class EmojiKitchenPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.metadata = None

    async def initialize(self):
        """异步初始化：下载或加载 metadata.json"""
        os.makedirs(CACHE_DIR, exist_ok=True)
        await self._load_metadata()

    async def _load_metadata(self):
        """加载 metadata，优先使用缓存"""
        need_download = True

        if os.path.exists(CACHE_FILE):
            file_age = time.time() - os.path.getmtime(CACHE_FILE)
            if file_age < CACHE_MAX_AGE:
                need_download = False

        if need_download:
            await self._download_metadata()

        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    self.metadata = json.load(f)
                logger.info(
                    "Emoji Kitchen: metadata loaded, %d supported emoji",
                    len(self.metadata.get("knownSupportedEmoji", [])),
                )
            except Exception as e:
                logger.error("Emoji Kitchen: failed to load metadata: %s", e)
                self.metadata = None
        else:
            logger.error("Emoji Kitchen: metadata.json not found")

    async def _download_metadata(self):
        """从多个镜像源尝试下载 metadata.json（8.5MB），带重试和完整性校验"""
        logger.info("Emoji Kitchen: downloading metadata.json ...")
        tmp_file = CACHE_FILE + ".tmp"
        timeout = aiohttp.ClientTimeout(total=60, connect=10)

        for url in METADATA_URLS:
            for attempt in range(1, 4):  # 每个镜像最多重试 3 次
                try:
                    logger.info(
                        "Emoji Kitchen: trying %s (attempt %d/3)",
                        url.split("/")[2], attempt,
                    )
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.get(url) as resp:
                            resp.raise_for_status()
                            # 流式下载，避免大文件占用过多内存
                            with open(tmp_file, "wb") as f:
                                async for chunk in resp.content.iter_chunked(65536):
                                    f.write(chunk)

                    # 校验 JSON 完整性
                    with open(tmp_file, "r", encoding="utf-8") as f:
                        json.load(f)

                    # 原子替换：临时文件 → 正式文件
                    os.replace(tmp_file, CACHE_FILE)
                    logger.info("Emoji Kitchen: metadata.json downloaded successfully")
                    return

                except json.JSONDecodeError:
                    logger.warning("Emoji Kitchen: downloaded file is not valid JSON, retrying...")
                except Exception as e:
                    logger.warning(
                        "Emoji Kitchen: failed from %s (attempt %d): %s",
                        url.split("/")[2], attempt, e,
                    )
                finally:
                    # 清理残留的临时文件
                    if os.path.exists(tmp_file):
                        try:
                            os.remove(tmp_file)
                        except OSError:
                            pass

        logger.error("Emoji Kitchen: all mirror sources failed after retries")

    def _find_combination(self, emoji1: str, emoji2: str) -> str | None:
        """查找两个 emoji 的合成图 URL，双向查找。

        Returns:
            合成图的 gStaticUrl，如果没有找到则返回 None
        """
        if not self.metadata:
            return None

        data = self.metadata.get("data", {})
        cp1 = emoji_to_codepoint(emoji1)
        cp2 = emoji_to_codepoint(emoji2)

        # Try left→right
        url = self._lookup(data, cp1, cp2)
        if url:
            return url

        # Try right→left
        url = self._lookup(data, cp2, cp1)
        if url:
            return url

        return None

    def _lookup(self, data: dict, left_cp: str, right_cp: str) -> str | None:
        """在 metadata 中查找指定 codepoint 组合"""
        left_data = data.get(left_cp)
        if not left_data:
            return None

        combinations = left_data.get("combinations", {})
        combo_list = combinations.get(right_cp)
        if not combo_list:
            return None

        # 找到 isLatest=true 的版本
        for combo in combo_list:
            if combo.get("isLatest", False):
                return combo.get("gStaticUrl")

        # 如果没有标记 isLatest 的，取第一个
        if combo_list:
            return combo_list[0].get("gStaticUrl")

        return None

    @filter.command("mix")
    async def mix_command(self, event: AstrMessageEvent):
        """合成两个 emoji：/mix 😀😺"""
        if not self.metadata:
            yield event.plain_result("⚠️ Emoji Kitchen 数据尚未加载，请稍后再试。")
            return

        text = event.message_str.strip()

        # 提取所有 emoji
        emoji_chars = [m.group(0) for m in EMOJI_ITER_PATTERN.finditer(text)]

        if len(emoji_chars) < 2:
            yield event.plain_result("请提供两个 emoji，例如：/mix 😀😺")
            return

        emoji1, emoji2 = emoji_chars[0], emoji_chars[1]
        url = self._find_combination(emoji1, emoji2)

        if url:
            chain = [Comp.Image.fromURL(url)]
            yield event.chain_result(chain)
        else:
            yield event.plain_result(
                f"😅 抱歉，{emoji1} + {emoji2} 这个组合暂不支持。\n试试其他 emoji 吧！"
            )

    @filter.event_message_type()
    async def auto_mix(self, event: AstrMessageEvent):
        """自动检测：当消息恰好是两个 emoji 时合成"""
        if not self.metadata:
            return

        text = event.message_str
        if not text:
            return

        m = TWO_EMOJI_MSG_PATTERN.match(text)
        if not m:
            return

        emoji1, emoji2 = m.group(1), m.group(2)
        url = self._find_combination(emoji1, emoji2)

        if url:
            chain = [Comp.Image.fromURL(url)]
            yield event.chain_result(chain)
        # 自动匹配模式下如果不支持则静默不回复

    async def terminate(self):
        """插件卸载时的清理"""
        pass

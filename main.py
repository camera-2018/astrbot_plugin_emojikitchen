import os
import json
import time
import asyncio
from pathlib import Path

import regex
import aiohttp

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

METADATA_URLS = [
    "https://raw.githubusercontent.com/xsalazar/emoji-kitchen-backend/main/app/metadata.json",
    "https://ghfast.top/https://raw.githubusercontent.com/xsalazar/emoji-kitchen-backend/main/app/metadata.json",
    "https://gh-proxy.com/https://raw.githubusercontent.com/xsalazar/emoji-kitchen-backend/main/app/metadata.json",
    "https://mirror.ghproxy.com/https://raw.githubusercontent.com/xsalazar/emoji-kitchen-backend/main/app/metadata.json",
]
CACHE_MAX_AGE = 7 * 24 * 3600  # 7 days

# Regex pattern to match a single emoji (including ZWJ sequences, skin tones,
# keycaps, flags, tag sequences, etc.)
SINGLE_EMOJI_RE = (
    r"(?:"
    r"(?:\p{Emoji_Presentation}|\p{Emoji}\uFE0F)"  # base emoji
    r"(?:\u200D(?:\p{Emoji_Presentation}|\p{Emoji}\uFE0F))*"  # ZWJ sequences
    r"[\U0001F3FB-\U0001F3FF]?"  # optional skin tone modifier
    r")"
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
        self._download_lock = asyncio.Lock()
        # 使用框架提供的数据目录
        self._data_dir = StarTools.get_data_dir("emoji_kitchen")
        self._cache_file = self._data_dir / "metadata.json"
        self._img_dir = self._data_dir / "images"

    def _get_proxy(self) -> str | None:
        """从环境变量获取 HTTP 代理。

        AstrBot 会将配置文件中的 http_proxy 写入环境变量，
        所以直接读取环境变量即可获取代理配置。
        """
        for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            proxy = os.environ.get(var)
            if proxy:
                return proxy
        return None

    async def initialize(self):
        """异步初始化：下载或加载 metadata.json"""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._img_dir.mkdir(parents=True, exist_ok=True)
        await self._load_metadata()

    async def _load_metadata(self):
        """加载 metadata，优先使用缓存"""
        need_download = True

        if self._cache_file.exists():
            file_age = time.time() - self._cache_file.stat().st_mtime
            if file_age < CACHE_MAX_AGE:
                need_download = False

        if need_download:
            await self._download_metadata()

        if self._cache_file.exists():
            try:
                self.metadata = json.loads(self._cache_file.read_text(encoding="utf-8"))
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
        tmp_file = str(self._cache_file) + ".tmp"
        timeout = aiohttp.ClientTimeout(total=60, connect=10)
        proxy = self._get_proxy()

        async with aiohttp.ClientSession(timeout=timeout) as session:
            for url in METADATA_URLS:
                for attempt in range(1, 4):  # 每个镜像最多重试 3 次
                    try:
                        logger.info(
                            "Emoji Kitchen: trying %s (attempt %d/3)",
                            url.split("/")[2], attempt,
                        )
                        async with session.get(url, proxy=proxy) as resp:
                            resp.raise_for_status()
                            # 流式下载，避免大文件占用过多内存
                            with open(tmp_file, "wb") as f:
                                async for chunk in resp.content.iter_chunked(65536):
                                    f.write(chunk)

                        # 校验 JSON 完整性
                        with open(tmp_file, "r", encoding="utf-8") as f:
                            json.load(f)

                        # 原子替换：临时文件 → 正式文件
                        os.replace(tmp_file, str(self._cache_file))
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

        # 找到 isLatest=true 且有有效 URL 的版本
        for combo in combo_list:
            if combo.get("isLatest", False):
                url = combo.get("gStaticUrl")
                if url:
                    return url
                # isLatest 但没有 URL，继续遍历其他版本

        # 没有有效的 isLatest 版本，取第一个有 URL 的
        for combo in combo_list:
            url = combo.get("gStaticUrl")
            if url:
                return url

        return None

    async def _download_image(self, url: str) -> str | None:
        """下载合成图到本地缓存，返回本地文件路径。

        gstatic.com 在国内可能无法直接访问，依次尝试：
        1. 直接下载（走代理如果有）
        2. WordPress Photon CDN
        3. wsrv.nl 图片代理
        4. images.weserv.nl 图片代理
        """
        # 从 URL 提取文件名作为缓存 key
        filename = url.rsplit("/", 1)[-1]
        local_path = self._img_dir / filename

        # 已缓存则直接返回
        if local_path.exists():
            return str(local_path)

        # 使用异步锁防止并发下载同一文件
        async with self._download_lock:
            # double-check：拿到锁后再次检查（可能被其他协程已下载）
            if local_path.exists():
                return str(local_path)

            # 构造镜像 URL 列表
            stripped = url.replace("https://", "").replace("http://", "")
            mirror_urls = [
                url,                                          # 直接下载（走代理）
                f"https://i0.wp.com/{stripped}",              # WordPress Photon CDN
                f"https://wsrv.nl/?url={stripped}",            # wsrv.nl 图片代理
                f"https://images.weserv.nl/?url={stripped}",   # weserv.nl 图片代理
            ]

            timeout = aiohttp.ClientTimeout(total=15, connect=5)
            tmp_path = str(local_path) + ".tmp"

            async with aiohttp.ClientSession(timeout=timeout) as session:
                for mirror_url in mirror_urls:
                    try:
                        # 只有直接访问时才使用代理，镜像源不需要
                        proxy = self._get_proxy() if mirror_url == url else None
                        async with session.get(mirror_url, proxy=proxy) as resp:
                            resp.raise_for_status()
                            content = await resp.read()
                            if len(content) < 100:
                                raise ValueError(f"response too small ({len(content)} bytes)")
                            # 原子写入：先写临时文件再替换
                            with open(tmp_path, "wb") as f:
                                f.write(content)
                            os.replace(tmp_path, str(local_path))
                            logger.info("Emoji Kitchen: image downloaded from %s", mirror_url.split("?")[0].split("/")[2])
                            return str(local_path)
                    except Exception as e:
                        logger.warning(
                            "Emoji Kitchen: image download failed from %s: %s",
                            mirror_url.split("?")[0].split("/")[2], e,
                        )
                        # 清理临时文件
                        if os.path.exists(tmp_path):
                            try:
                                os.remove(tmp_path)
                            except OSError:
                                pass

            logger.error("Emoji Kitchen: all image sources failed: %s", filename)
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

        if not url:
            yield event.plain_result(
                f"😅 抱歉，{emoji1} + {emoji2} 这个组合暂不支持。\n试试其他 emoji 吧！"
            )
            return

        local_path = await self._download_image(url)
        if local_path:
            chain = [Comp.Image.fromFileSystem(local_path)]
            yield event.chain_result(chain)
        else:
            yield event.plain_result("⚠️ 图片下载失败，请稍后再试。")

    @filter.event_message_type(EventMessageType.ALL)
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

        if not url:
            return

        local_path = await self._download_image(url)
        if local_path:
            chain = [Comp.Image.fromFileSystem(local_path)]
            yield event.chain_result(chain)
        # 自动匹配模式下下载失败则静默

    async def terminate(self):
        """插件卸载时的清理"""
        pass

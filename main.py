import os
import json
import time
import asyncio
import hashlib
from urllib.parse import urlparse

import regex
import aiohttp

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, StarTools
from astrbot.api import logger
import astrbot.api.message_components as Comp

METADATA_URLS = [
    "https://raw.githubusercontent.com/xsalazar/emoji-kitchen-backend/main/app/metadata.json",
    "https://ghfast.top/https://raw.githubusercontent.com/xsalazar/emoji-kitchen-backend/main/app/metadata.json",
    "https://gh-proxy.com/https://raw.githubusercontent.com/xsalazar/emoji-kitchen-backend/main/app/metadata.json",
    "https://mirror.ghproxy.com/https://raw.githubusercontent.com/xsalazar/emoji-kitchen-backend/main/app/metadata.json",
]
CACHE_MAX_AGE = 7 * 24 * 3600  # 7 days
MAX_METADATA_BYTES = 20 * 1024 * 1024  # 20 MB

# Regex: 匹配单个完整 emoji（含 ZWJ 序列、肤色修饰符、旗帜、keycap 等）
SINGLE_EMOJI_RE = (
    r"(?:"
    # 旗帜: regional indicator 对 🇺🇸
    r"[\U0001F1E0-\U0001F1FF]{2}"
    r"|"
    # keycap: digit/symbol + FE0F + 20E3  (0️⃣ ~ 9️⃣, *️⃣, #️⃣)
    r"[0-9#*]\uFE0F?\u20E3"
    r"|"
    # 通用 emoji (含 ZWJ 序列 + 可选肤色修饰符)
    r"(?:\p{Emoji_Presentation}|\p{Emoji}\uFE0F)"
    r"(?:[\U0001F3FB-\U0001F3FF])?"                          # 可选肤色
    r"(?:\u200D(?:\p{Emoji_Presentation}|\p{Emoji}\uFE0F)"
    r"(?:[\U0001F3FB-\U0001F3FF])?)*"                        # ZWJ + 肤色
    r")"
)

# Compiled patterns
EMOJI_ITER_PATTERN = regex.compile(SINGLE_EMOJI_RE)
TWO_EMOJI_MSG_PATTERN = regex.compile(
    rf"^\s*({SINGLE_EMOJI_RE})\s*({SINGLE_EMOJI_RE})\s*$"
)

# 图片 Content-Type 白名单
IMAGE_CONTENT_TYPES = {"image/png", "image/webp", "image/jpeg", "image/gif"}

# SSRF 防护：仅允许从 *.gstatic.com 下载 emoji 图片
_ALLOWED_IMAGE_HOST_SUFFIX = ".gstatic.com"
# 单张图片最大允许字节数（防止内存耗尽）
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB

# 图片文件头（magic number）校验签名
# 格式：(prefix_bytes, prefix_slice, optional_second_bytes, optional_second_slice)
_IMAGE_MAGIC_SIGNATURES = [
    (b'\x89PNG\r\n\x1a\n', slice(0, 8), None, None),     # PNG
    (b'\xff\xd8\xff', slice(0, 3), None, None),           # JPEG
    (b'GIF87a', slice(0, 6), None, None),                  # GIF87a
    (b'GIF89a', slice(0, 6), None, None),                  # GIF89a
    (b'RIFF', slice(0, 4), b'WEBP', slice(8, 12)),         # WebP
]


def _is_valid_image_magic(data: bytes) -> bool:
    """通过文件头校验数据是否为已知图片格式。"""
    for magic1, s1, magic2, s2 in _IMAGE_MAGIC_SIGNATURES:
        if data[s1] == magic1:
            if magic2 is None or data[s2] == magic2:
                return True
    return False


def _is_allowed_image_url(url: str) -> bool:
    """SSRF guard: 仅允许 HTTPS 协议且域名属于 *.gstatic.com。"""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme != "https":
        return False
    host = parsed.netloc.lower().split(":")[0]  # 去除端口
    return host == "www.gstatic.com" or host.endswith(_ALLOWED_IMAGE_HOST_SUFFIX)


def emoji_to_codepoint(emoji_char: str) -> str:
    """Convert an emoji character (possibly multi-codepoint) to dash-separated hex codepoints.

    Example: '😀' -> '1f600', '👨‍🍳' -> '1f468-200d-1f373'
    """
    return "-".join(f"{ord(c):x}" for c in emoji_char)


def _url_to_cache_filename(url: str) -> str:
    """将 URL 转换为安全的缓存文件名（hash + 原始后缀）。"""
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    # 通过 urlparse 取 path 部分，避免 query 字符串混入扩展名
    _, ext = os.path.splitext(urlparse(url).path)
    return f"{url_hash}{ext or '.png'}"


class EmojiKitchenPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.metadata = None
        # 按文件名分段锁，避免全局锁阻塞不相关下载；下载完成后清理
        self._download_locks: dict[str, asyncio.Lock] = {}
        # 使用框架提供的数据目录
        self._data_dir = StarTools.get_data_dir("emoji_kitchen")
        self._cache_file = self._data_dir / "metadata.json"
        self._img_dir = self._data_dir / "images"

    def _get_proxy(self) -> str | None:
        """从环境变量获取 HTTP 代理。"""
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
        """加载 metadata，优先使用缓存。缓存损坏时自动重新下载。"""
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
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("Emoji Kitchen: cached metadata corrupted (%s), re-downloading...", e)
                self.metadata = None
                # 缓存损坏 → 删除并重新下载
                try:
                    self._cache_file.unlink()
                except OSError:
                    pass
                await self._download_metadata()
                # 再次尝试加载
                if self._cache_file.exists():
                    try:
                        self.metadata = json.loads(self._cache_file.read_text(encoding="utf-8"))
                        logger.info("Emoji Kitchen: metadata re-loaded after re-download")
                    except Exception:
                        logger.error("Emoji Kitchen: metadata still corrupted after re-download")
                        self.metadata = None
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
                for attempt in range(1, 4):
                    try:
                        logger.info(
                            "Emoji Kitchen: trying %s (attempt %d/3)",
                            url.split("/")[2], attempt,
                        )
                        async with session.get(url, proxy=proxy) as resp:
                            resp.raise_for_status()
                            total_meta = 0
                            with open(tmp_file, "wb") as f:
                                async for chunk in resp.content.iter_chunked(65536):
                                    total_meta += len(chunk)
                                    if total_meta > MAX_METADATA_BYTES:
                                        raise ValueError(
                                            f"metadata too large (>{MAX_METADATA_BYTES} bytes)"
                                        )
                                    f.write(chunk)

                        # 校验 JSON 完整性
                        with open(tmp_file, "r", encoding="utf-8") as f:
                            json.load(f)

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
                        if os.path.exists(tmp_file):
                            try:
                                os.remove(tmp_file)
                            except OSError:
                                pass

        logger.error("Emoji Kitchen: all mirror sources failed after retries")

    def _find_combination(self, emoji1: str, emoji2: str) -> str | None:
        """查找两个 emoji 的合成图 URL，双向查找。"""
        if not self.metadata:
            return None

        data = self.metadata.get("data", {})
        cp1 = emoji_to_codepoint(emoji1)
        cp2 = emoji_to_codepoint(emoji2)

        url = self._lookup(data, cp1, cp2)
        if url:
            return url

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
        # SSRF 防护：仅允许向白名单域名发起请求
        if not _is_allowed_image_url(url):
            logger.warning("Emoji Kitchen: blocked download from disallowed URL: %s", url)
            return None

        filename = _url_to_cache_filename(url)
        local_path = self._img_dir / filename

        # 已缓存则直接返回
        if local_path.exists():
            return str(local_path)

        # 按文件名分段锁，仅阻塞同一文件的并发下载。
        # 锁对象不从字典中删除：在 finally 中 pop 会引入竞态——协程 A 释放锁并
        # pop 之后，协程 B 尚未被调度、协程 C 已创建新锁并开始下载，导致同一文件
        # 被并发重复下载。保留锁对象可消除该竞态；内存开销可忽略（受 emoji 组合数
        # 上界约束）。
        if filename not in self._download_locks:
            self._download_locks[filename] = asyncio.Lock()
        lock = self._download_locks[filename]

        async with lock:
            # double-check：拿到锁后再次检查
            if local_path.exists():
                return str(local_path)

            stripped = url.replace("https://", "").replace("http://", "")
            mirror_urls = [
                url,
                f"https://i0.wp.com/{stripped}",
                f"https://wsrv.nl/?url={stripped}",
                f"https://images.weserv.nl/?url={stripped}",
            ]

            timeout = aiohttp.ClientTimeout(total=15, connect=5)
            tmp_path = str(local_path) + ".tmp"

            async with aiohttp.ClientSession(timeout=timeout) as session:
                for mirror_url in mirror_urls:
                    try:
                        proxy = self._get_proxy() if mirror_url == url else None
                        async with session.get(mirror_url, proxy=proxy) as resp:
                            resp.raise_for_status()

                            # SSRF 防护：校验重定向后的最终 URL（仅针对直连分支）
                            if mirror_url == url:
                                final_url = str(resp.url)
                                if not _is_allowed_image_url(final_url):
                                    raise ValueError(
                                        f"redirect to disallowed host: {final_url}"
                                    )

                            # 校验 Content-Type（空值视为不合法，不放行）
                            content_type = resp.content_type or ""
                            if not content_type or content_type not in IMAGE_CONTENT_TYPES:
                                raise ValueError(f"unexpected content-type: {content_type!r}")

                            # 分块读取，防止超大响应占满内存
                            chunks = []
                            total = 0
                            async for chunk in resp.content.iter_chunked(65536):
                                total += len(chunk)
                                if total > MAX_IMAGE_BYTES:
                                    raise ValueError(f"response too large (>{MAX_IMAGE_BYTES} bytes)")
                                chunks.append(chunk)
                            content = b"".join(chunks)

                            if len(content) < 100:
                                raise ValueError(f"response too small ({len(content)} bytes)")

                            # 文件头（magic number）校验，防止非图片内容被缓存
                            if not _is_valid_image_magic(content):
                                raise ValueError("invalid image magic number")

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

        # 严格校验：消息（去除首尾空白后）必须「仅包含」恰好两个 emoji，
        # 不允许夹杂其他文字，与用户提示语义保持一致。
        m = TWO_EMOJI_MSG_PATTERN.match(text)
        if not m:
            yield event.plain_result("请提供恰好两个 emoji，例如：/mix 😀😺")
            return

        emoji1, emoji2 = m.group(1), m.group(2)
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

        # 快速前置过滤：两个 emoji 的消息不会超过 100 个字符
        if len(text) > 100:
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

    async def terminate(self):
        """插件卸载时的清理"""
        pass

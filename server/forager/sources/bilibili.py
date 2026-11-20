"""Bilibili 源适配器。

三个绕不过去的点：

1. **WBI 签名**：`/x/web-interface/wbi/*` 与 `/x/player/wbi/playurl` 需要
   `wts` + `w_rid`，密钥来自 `/x/web-interface/nav` 的 `wbi_img`，按 64 位置换表重排后取前 32 位。
2. **dash 音视频分离**：高清晰度返回两条独立 fMP4 轨，必须 ffmpeg 合并；
   而浏览器 `<video>` 播不了分离轨，所以**流代理单独走 `fnval=1` 的 durl 渐进式地址**。
3. **buvid3**：匿名请求也要带，否则 WBI 接口常回 `-352` 风控；启动时从
   `/x/frontend/finger/spi` 自取，无需登录。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from typing import Any, ClassVar
from urllib.parse import urlencode

from .base import BaseSource, SourceError

API = "https://api.bilibili.com"
PASSPORT = "https://passport.bilibili.com"
WEB = "https://www.bilibili.com/"

#: WBI mixin key 的固定置换表（64 位，公开常量）
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

#: 清晰度 id → 展示标签（越大越清晰）
QN_LABELS: dict[int, str] = {
    127: "8K",
    126: "杜比视界",
    125: "HDR",
    120: "4K",
    116: "1080P60",
    112: "1080P+",
    80: "1080P",
    74: "720P60",
    64: "720P",
    32: "480P",
    16: "360P",
    6: "240P",
}
LABEL_QN: dict[str, int] = {label: qn for qn, label in QN_LABELS.items()}

#: 主分区（tid → 名称）。B 站分区体系相对稳定，用静态表避免多打一次接口
PARTITIONS: list[dict[str, str]] = [
    {"id": "1", "name": "动画"},
    {"id": "13", "name": "番剧"},
    {"id": "167", "name": "国创"},
    {"id": "3", "name": "音乐"},
    {"id": "129", "name": "舞蹈"},
    {"id": "4", "name": "游戏"},
    {"id": "36", "name": "知识"},
    {"id": "188", "name": "科技"},
    {"id": "234", "name": "运动"},
    {"id": "223", "name": "汽车"},
    {"id": "160", "name": "生活"},
    {"id": "211", "name": "美食"},
    {"id": "217", "name": "动物圈"},
    {"id": "119", "name": "鬼畜"},
    {"id": "155", "name": "时尚"},
    {"id": "5", "name": "娱乐"},
    {"id": "181", "name": "影视"},
    {"id": "202", "name": "资讯"},
    {"id": "23", "name": "电影"},
    {"id": "11", "name": "电视剧"},
    {"id": "177", "name": "纪录片"},
    {"id": "71", "name": "综艺"},
]

API_ERRORS: dict[int, str] = {
    -101: "Bilibili 未登录（需要 SESSDATA 才能访问高清晰度）",
    -352: "Bilibili 风控校验失败（-352）：检查 Cookie/buvid3，或稍后重试",
    -412: "Bilibili 请求被拦截（-412）：请求过于频繁，请调大 HANIME1/BILIBILI 的请求间隔",
    -404: "稿件不存在或已删除",
    62002: "稿件不可见（可能被删除或设为私密）",
    62004: "稿件审核中",
    62012: "仅 UP 主自己可见",
}


# ---- WBI 签名（纯函数，便于单测）----

def mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def sign_query(params: dict[str, Any], mixin: str, wts: int | None = None) -> str:
    """按 B 站规则签名：去特殊字符 → 按 key 排序 → urlencode → 拼 mixin key 取 md5。"""
    items = dict(params)
    items["wts"] = int(wts if wts is not None else time.time())
    cleaned = []
    for key in sorted(items):
        value = re.sub(r"[!'()*]", "", str(items[key]))
        cleaned.append((key, value))
    query = urlencode(cleaned)
    digest = hashlib.md5((query + mixin).encode()).hexdigest()
    return f"{query}&w_rid={digest}"


# ---- 纯解析（便于离线验证）----

def build_qualities(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """playurl 响应 → 清晰度列表（含 dash 音轨地址）。"""
    dash = payload.get("dash") or {}
    videos = dash.get("video") or []
    audios = dash.get("audio") or []

    audio_url = None
    if audios:
        best_audio = max(audios, key=lambda a: a.get("bandwidth") or 0)
        audio_url = best_audio.get("baseUrl") or best_audio.get("base_url")

    # 同一清晰度常同时给 avc / hevc / av1 多条轨：优先 avc1，
    # 因为电视浏览器与移动端对 hevc 的支持参差，选了它可能「有画面没声音」地播不出来。
    groups: dict[int, list[dict[str, Any]]] = {}
    for track in videos:
        url = track.get("baseUrl") or track.get("base_url")
        if not url:
            continue
        groups.setdefault(int(track.get("id") or 0), []).append(track)

    qualities: list[dict[str, Any]] = []
    for qn in sorted(groups, reverse=True):
        tracks = groups[qn]
        track = next(
            (t for t in tracks if str(t.get("codecs") or "").startswith("avc1")),
            tracks[0],
        )
        item: dict[str, Any] = {
            "label": QN_LABELS.get(qn, str(qn)),
            "url": track.get("baseUrl") or track.get("base_url"),
            "qn": qn,
        }
        if audio_url:
            item["audioUrl"] = audio_url
        qualities.append(item)

    # 没有 dash（低清晰度/老稿件）时退回 durl 渐进式
    if not qualities:
        for segment in payload.get("durl") or []:
            url = segment.get("url")
            if url:
                qualities.append({"label": "auto", "url": url, "qn": 0})
                break
    return qualities


def build_detail(
    source_id: str,
    view: dict[str, Any],
    play: dict[str, Any],
    *,
    web: str = WEB,
) -> dict[str, Any]:
    qualities = build_qualities(play)
    if not qualities:
        raise SourceError("播放地址解析失败（可能需要登录或该稿件受限）", 502)

    bvid = view.get("bvid") or ""
    best = qualities[0]
    owner = view.get("owner") or {}
    stat = view.get("stat") or {}
    pubdate = view.get("pubdate")

    detail: dict[str, Any] = {
        "id": bvid,
        "source": source_id,
        "title": (view.get("title") or bvid).strip(),
        "pageUrl": f"{web}video/{bvid}",
        "streamUrl": best["url"],
        "streamProxyUrl": (
            f"/api/sources/{source_id}/stream?v={bvid}&quality={best['label']}"
        ),
        "qualities": qualities,
        "related": [],
        "tags": [view["tname"]] if view.get("tname") else [],
        "category": view.get("tname"),
        "durationSec": view.get("duration"),
        "views": stat.get("view"),
    }
    pic = view.get("pic")
    if pic:
        detail["coverUrl"] = _https(pic)
    if owner.get("name"):
        detail["uploader"] = owner["name"]
    if view.get("desc"):
        detail["description"] = view["desc"]
    if pubdate:
        detail["uploadDate"] = time.strftime("%Y-%m-%d", time.localtime(int(pubdate)))
    return detail


def parse_bvid(vid: str) -> tuple[str | None, int | None]:
    """接受 BV 号 / av 号 / 纯数字 aid / 视频链接。"""
    raw = (vid or "").strip()
    match = re.search(r"(BV[0-9A-Za-z]{10})", raw)
    if match:
        return match.group(1), None
    match = re.search(r"(?:^|[^a-z])av(\d+)", raw, re.IGNORECASE) or re.fullmatch(r"(\d+)", raw)
    if match:
        return None, int(match.group(1))
    return None, None


# ---- 适配器 ----

class BilibiliSource(BaseSource):
    id: ClassVar[str] = "bilibili"
    name: ClassVar[str] = "Bilibili"
    image_hosts: ClassVar[tuple[str, ...]] = (
        "hdslb.com",
        "biliimg.com",
        "bilivideo.com",
        "bilibili.com",
        "bili2233.cn",
    )
    config_fields: ClassVar[tuple[str, ...]] = ("cookie", "proxy", "quality", "min_interval_ms")
    kinds: ClassVar[tuple[str, ...]] = ("query", "partition", "uploader", "favorites", "following")
    quality_options: ClassVar[tuple[str, ...]] = ("8K", "4K", "1080P60", "1080P", "720P", "480P", "360P")
    supports_qrcode: ClassVar[bool] = True

    def __init__(self, client) -> None:
        super().__init__(client)
        self._lock = asyncio.Lock()
        self._session_ready = False
        self._mixin: tuple[float, str] | None = None

    def apply_config(self, config: dict[str, Any]) -> None:
        super().apply_config(config)
        # Cookie 变了要重新走一遍 buvid3 补全与 WBI 取键
        if "cookie" in config:
            self._session_ready = False
            self._mixin = None

    # ---- 契约方法 ----

    async def list(self, page: int = 1, category: str | None = None) -> dict[str, Any]:
        page_no = max(1, int(page or 1))
        try:
            data = await self._get("/x/web-interface/popular", {"pn": page_no, "ps": 20}, ttl_ms=120_000)
            items = [self._summary(item) for item in data.get("list") or []]
            return {"items": items, "page": page_no, "hasMore": bool(items)}
        except SourceError:
            # popular 在部分网络下会 -412，退回排行榜（公开且稳定）
            data = await self._get("/x/web-interface/ranking/v2", {"rid": 0, "type": "all"}, ttl_ms=600_000)
            items = [self._summary(item) for item in data.get("list") or []]
            return {"items": items, "page": page_no, "hasMore": False}

    async def search(self, keyword: str, page: int = 1) -> dict[str, Any]:
        await self._ensure_session()
        data = await self._get_wbi(
            "/x/web-interface/wbi/search/type",
            {
                "search_type": "video",
                "keyword": keyword,
                "page": max(1, int(page or 1)),
                "page_size": 20,
            },
            ttl_ms=120_000,
        )
        items = [
            self._search_summary(item)
            for item in data.get("result") or []
            if item.get("bvid")
        ]
        return {"items": items, "page": max(1, int(page or 1)), "hasMore": bool(items)}

    async def detail(self, vid: str) -> dict[str, Any]:
        bvid, aid = parse_bvid(vid)
        if not bvid and not aid:
            raise SourceError(f"无效的视频 ID：{vid}", 400)

        view = await self._view(bvid, aid)
        cid = view.get("cid")
        if not cid:
            raise SourceError("稿件不可用（可能已删除或需要登录）", 404)

        play = await self._get_wbi(
            "/x/player/wbi/playurl",
            {"bvid": view.get("bvid"), "cid": cid, "qn": 127, "fnval": 4048, "fourk": 1},
            ttl_ms=120_000,
        )
        return build_detail(self.id, view, play)

    async def stream_url(self, vid: str, quality: str | None = None) -> str:
        """浏览器的 `<video>` 只能吃渐进式单文件，因此这里走 fnval=1 的 durl。"""
        bvid, aid = parse_bvid(vid)
        if not bvid and not aid:
            raise SourceError(f"无效的视频 ID：{vid}", 400)

        view = await self._view(bvid, aid)
        cid = view.get("cid")
        if not cid:
            raise SourceError("稿件不可用（可能已删除或需要登录）", 404)

        qn = LABEL_QN.get(quality or "", 80)
        play = await self._get_wbi(
            "/x/player/wbi/playurl",
            {"bvid": view.get("bvid"), "cid": cid, "qn": qn, "fnval": 1, "fourk": 1},
            ttl_ms=120_000,
        )
        for segment in play.get("durl") or []:
            if segment.get("url"):
                return segment["url"]
        # 兜底：dash 视频轨（无音轨，仅保证能出画面）
        qualities = build_qualities(play)
        if qualities:
            return qualities[0]["url"]
        raise SourceError("该稿件没有可用的播放地址（可能需要登录）", 404)

    # ---- 线索树：发现能力 ----

    def categories(self) -> list[dict[str, str]]:
        return PARTITIONS

    async def partition_items(self, value: str, page: int = 1) -> dict[str, Any]:
        """分区发现：用排行榜，天然按热度排序，适合持续盯。"""
        data = await self._get(
            "/x/web-interface/ranking/v2", {"rid": value or "0", "type": "all"}, ttl_ms=300_000
        )
        items = [self._summary(v) for v in data.get("list") or []]
        return {"items": items, "page": 1, "hasMore": False}

    async def uploader_items(self, value: str, page: int = 1) -> dict[str, Any]:
        """UP 主投稿：需要 WBI 签名；未登录时常吃 -352。"""
        await self._ensure_session()
        data = await self._get_wbi(
            "/x/space/wbi/arc/search",
            {"mid": value, "pn": max(1, int(page or 1)), "ps": 30, "order": "pubdate"},
            ttl_ms=120_000,
        )
        vlist = ((data.get("list") or {}).get("vlist")) or []
        return {"items": [self._space_summary(v) for v in vlist], "page": page, "hasMore": bool(vlist)}

    async def favorite_uploaders(self) -> list[dict[str, str]]:
        """收藏夹 → 出现过作品的发布者（去重）。"""
        mid = await self._self_mid()
        folders = await self._get("/x/v3/fav/folder/created/list-all", {"up_mid": mid}, ttl_ms=0)
        found: dict[int, str] = {}
        for folder in folders.get("list") or []:
            media_id = folder.get("id")
            if not media_id:
                continue
            page = 1
            while page <= 20:
                res = await self._get(
                    "/x/v3/fav/resource/list",
                    {"media_id": media_id, "pn": page, "ps": 20, "platform": "web"},
                    ttl_ms=0,
                )
                for item in res.get("medias") or []:
                    upper = item.get("upper") or {}
                    if upper.get("mid"):
                        found[int(upper["mid"])] = upper.get("name") or str(upper["mid"])
                if not res.get("has_more"):
                    break
                page += 1
        return [{"id": str(k), "title": v} for k, v in found.items()]

    async def following_uploaders(self) -> list[dict[str, str]]:
        mid = await self._self_mid()
        found: dict[int, str] = {}
        page = 1
        while page <= 10:
            data = await self._get(
                "/x/relation/followings", {"vmid": mid, "pn": page, "ps": 50}, ttl_ms=0
            )
            users = data.get("list") or []
            for user in users:
                if user.get("mid"):
                    found[int(user["mid"])] = user.get("uname") or str(user["mid"])
            if len(users) < 50:
                break
            page += 1
        return [{"id": str(k), "title": v} for k, v in found.items()]

    async def _self_mid(self) -> int:
        await self._ensure_session()
        data = await self._get("/x/web-interface/nav", allow={-101})
        mid = data.get("mid")
        if not mid:
            raise SourceError("需要登录：请先在设置页配置 Cookie 或扫码登录", 401)
        return int(mid)

    def _space_summary(self, video: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": video.get("bvid") or "",
            "source": self.id,
            "title": _text(video.get("title")),
        }
        if video.get("pic"):
            out["coverUrl"] = _https(video["pic"])
        seconds = _duration_seconds(video.get("length"))
        if seconds:
            out["durationSec"] = seconds
        if video.get("play") is not None:
            out["views"] = video["play"]
        if video.get("author"):
            out["uploader"] = video["author"]
        return out

    # ---- 扫码登录 ----

    async def qrcode_generate(self) -> dict[str, Any]:
        data = await self._json(f"{PASSPORT}/x/passport-login/web/qrcode/generate", 0)
        return {"key": data.get("qrcode_key") or "", "url": data.get("url") or ""}

    async def qrcode_poll(self, key: str) -> dict[str, Any]:
        """data.code：0 成功 / 86101 未扫码 / 86090 已扫待确认 / 86038 已过期。"""
        from urllib.parse import quote

        data = await self._json(
            f"{PASSPORT}/x/passport-login/web/qrcode/poll?qrcode_key={quote(key)}", 0
        )
        status = int(data.get("code") or 0)
        result: dict[str, Any] = {"status": status, "message": data.get("message") or ""}
        cookie = _cookie_from_login_url(data.get("url") or "") if status == 0 else ""
        if cookie:
            result["cookie"] = cookie
        return result

    # ---- 内部请求 ----

    async def _view(self, bvid: str | None, aid: int | None) -> dict[str, Any]:
        params = {"bvid": bvid} if bvid else {"aid": aid}
        return await self._get("/x/web-interface/view", params)

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        ttl_ms: int = 60_000,
        allow: set[int] | frozenset[int] = frozenset(),
    ) -> dict[str, Any]:
        url = API + path
        if params:
            url += "?" + urlencode(params)
        return await self._json(url, ttl_ms, allow=allow)

    async def _get_wbi(self, path: str, params: dict[str, Any], ttl_ms: int = 60_000) -> dict[str, Any]:
        mixin = await self._ensure_mixin()
        return await self._json(f"{API}{path}?{sign_query(params, mixin)}", ttl_ms)

    async def _json(
        self, url: str, ttl_ms: int, *, allow: set[int] | frozenset[int] = frozenset()
    ) -> dict[str, Any]:
        text = await self.client.fetch_html(url, ttl_ms=ttl_ms)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceError("Bilibili 返回了非 JSON 响应（接口可能已调整）", 502) from exc
        code = payload.get("code", 0)
        # nav 等接口在未登录时返回 -101，但 data.wbi_img 仍然可用，需要放行
        if code != 0 and code not in allow:
            raise SourceError(
                API_ERRORS.get(code, payload.get("message") or f"接口错误 {code}"),
                404 if code == -404 else 502,
            )
        return payload.get("data") or {}

    async def _ensure_mixin(self) -> str:
        async with self._lock:
            if self._mixin and self._mixin[0] > time.time():
                return self._mixin[1]
            data = await self._get("/x/web-interface/nav", allow={-101})
            wbi = data.get("wbi_img") or {}
            img = _key_from_url(wbi.get("img_url") or "")
            sub = _key_from_url(wbi.get("sub_url") or "")
            if not img or not sub:
                raise SourceError("获取 WBI 密钥失败（B 站接口可能已调整）", 502)
            key = mixin_key(img, sub)
            self._mixin = (time.time() + 6 * 3600, key)
            return key

    async def _ensure_session(self) -> None:
        """匿名也需要 buvid3，否则 WBI 接口常回 -352。"""
        async with self._lock:
            if self._session_ready:
                return
            self._session_ready = True
            if "buvid3=" in (self.client.cookie or ""):
                return
            try:
                data = await self._get("/x/frontend/finger/spi", ttl_ms=0)
            except SourceError:
                return  # 拿不到就继续，让上游自己报错
            buvid = data.get("b_3")
            if buvid:
                cookie = self.client.cookie or ""
                self.client.cookie = (cookie + "; " if cookie else "") + f"buvid3={buvid}"

    # ---- 归一化 ----

    def _summary(self, item: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": item.get("bvid") or "",
            "source": self.id,
            "title": _text(item.get("title")),
        }
        if item.get("pic"):
            out["coverUrl"] = _https(item["pic"])
        duration = item.get("duration")
        seconds = _duration_seconds(duration)
        if seconds:
            out["durationSec"] = seconds
        views = (item.get("stat") or {}).get("view")
        if views is not None:
            out["views"] = views
        owner = item.get("owner") or {}
        if owner.get("name"):
            out["uploader"] = owner["name"]
        return out

    def _search_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": item.get("bvid") or "",
            "source": self.id,
            "title": _text(item.get("title")),
        }
        if item.get("pic"):
            out["coverUrl"] = _https(item["pic"])
        seconds = _duration_seconds(item.get("duration"))
        if seconds:
            out["durationSec"] = seconds
        if item.get("play") is not None:
            out["views"] = item["play"]
        if item.get("author"):
            out["uploader"] = item["author"]
        return out


# ---- 工具 ----

def _cookie_from_login_url(url: str) -> str:
    """登录成功后的跳转 URL 里带着凭据，拆成标准 Cookie 串。"""
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(url).query)
    parts = []
    for name in ("SESSDATA", "bili_jct", "DedeUserID"):
        value = (query.get(name) or [""])[0]
        if value:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def _key_from_url(url: str) -> str:
    name = url.rsplit("/", 1)[-1]
    return name.split(".")[0]


def _https(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _text(value: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value or "")).strip()


def _duration_seconds(value: Any) -> int | None:
    if isinstance(value, int):
        return value or None
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", value.strip())
    if not match:
        return None
    hours = int(match.group(1)) if match.group(1) else 0
    return hours * 3600 + int(match.group(2)) * 60 + int(match.group(3))

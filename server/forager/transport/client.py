"""上游抓取客户端（移植自 libre-tv 的 sources/http.ts）。

职责：
- 统一 UA / Referer / Cookie（过 Cloudflare 用）
- 每主机串行频控（避免电视端快速导航把源站打挂）
- 短 TTL 的 HTML 缓存
- DoH 自解析 + --resolve 直连 IPv4（本地 DNS 被污染时仍可访问）
"""

from __future__ import annotations

import asyncio
import json
import time
from urllib.parse import urljoin, urlparse

from .curl import CurlError, CurlResponse, StreamResponse, curl_request, open_stream


class UpstreamError(CurlError):
    """上游语义错误（HTTP 非 2xx / 解析失败），带状态码供路由映射。"""


DOH_ENDPOINTS = (
    "https://1.1.1.1/dns-query",
    "https://doh.pub/dns-query",
    "https://dns.google/resolve",
)


class UpstreamClient:
    def __init__(
        self,
        *,
        base_url: str,
        user_agent: str,
        proxy: str | None = None,
        dns_proxy: str | None = None,
        pinned_ip: str | None = None,
        cookie: str | None = None,
        min_interval_ms: int = 400,
        cache_ttl_ms: int = 60_000,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.proxy = proxy
        self.dns_proxy = dns_proxy
        self.pinned_ip = pinned_ip
        self.cookie = cookie
        self.min_interval = min_interval_ms / 1000
        self.cache_ttl = cache_ttl_ms / 1000

        self._lock = asyncio.Lock()      # 串行化上游请求
        self._last_at = 0.0
        self._cache: dict[str, tuple[float, str]] = {}
        self._dns_cache: dict[str, tuple[str, float]] = {}

    # ---- 公共请求 ----

    def _base_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Referer": self.base_url + "/",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    async def fetch_html(self, path: str, ttl_ms: int | None = None) -> str:
        """抓 HTML（走缓存与频控）。"""
        url = urljoin(self.base_url + "/", path)
        ttl = self.cache_ttl if ttl_ms is None else ttl_ms / 1000

        if ttl > 0:
            hit = self._cache.get(url)
            if hit and hit[0] > time.monotonic():
                return hit[1]

        async with self._lock:
            await self._throttle()
            resp = await curl_request(
                url,
                headers=self._base_headers(),
                proxy=self.proxy,
                retries=1,
                resolve_map=await self._resolve_map(url),
            )

        if not 200 <= resp.status < 300:
            hint = ""
            if resp.status == 403:
                hint = (
                    "（Cloudflare 拦截：请检查 HANIME1_COOKIE 的 cf_clearance 是否过期，"
                    "或替换 HANIME1_PROXY）"
                )
            raise UpstreamError(f"上游返回 HTTP {resp.status}{hint}", resp.status or 502)

        body = resp.text
        if ttl > 0:
            if len(self._cache) > 200:
                now = time.monotonic()
                self._cache = {k: v for k, v in self._cache.items() if v[0] >= now}
            self._cache[url] = (time.monotonic() + ttl, body)
        return body

    async def fetch_bytes(self, url: str, headers: dict[str, str] | None = None) -> CurlResponse:
        """抓二进制（图片代理用）：不缓存不频控。"""
        return await curl_request(
            url,
            headers={**self._base_headers(), **(headers or {})},
            proxy=self.proxy,
            range_=(headers or {}).get("range"),
            timeout=60,
            resolve_map=await self._resolve_map(url),
        )

    async def stream(self, url: str, range_: str | None = None) -> StreamResponse:
        """流式抓取（视频直放）：Range 透传，不落盘。"""
        return await open_stream(
            url,
            headers=self._base_headers(),
            proxy=self.proxy,
            range_=range_,
            timeout=60,
            resolve_map=await self._resolve_map(url),
        )

    # ---- 频控 ----

    async def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_at)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_at = time.monotonic()

    # ---- DNS ----

    async def _resolve_map(self, url: str) -> dict[str, str]:
        if self.proxy:
            return {}
        host = urlparse(url).hostname or ""
        if "." not in host:
            return {}
        try:
            return {host: await self._resolve_ipv4(host)}
        except Exception:
            return {}

    async def _resolve_ipv4(self, host: str) -> str:
        """DoH 自解析：本地 DNS 常被污染（解析到无关 IP）。

        DoH 查询本身在直连不可达/被污染时也走 dns_proxy；站点访问仍走直连 IPv4。
        """
        if self.pinned_ip:
            return self.pinned_ip

        cached = self._dns_cache.get(host)
        if cached and cached[1] > time.monotonic():
            return cached[0]

        last_error: Exception | None = None
        for endpoint in DOH_ENDPOINTS:
            try:
                resp = await curl_request(
                    f"{endpoint}?name={host}&type=A",
                    headers={"accept": "application/dns-json"},
                    proxy=self.dns_proxy,
                    timeout=8,
                )
                data = json.loads(resp.text or "{}")
                answers = data.get("Answer") or []
                if isinstance(answers, dict):
                    answers = [answers]
                for answer in answers:
                    if answer.get("type") == 1 and _is_ipv4(answer.get("data", "")):
                        self._dns_cache[host] = (answer["data"], time.monotonic() + 600)
                        return answer["data"]
            except Exception as exc:  # noqa: BLE001 —— 逐个端点降级
                last_error = exc
        raise UpstreamError(
            f"DoH 解析失败：{host}（{last_error or '无有效 A 记录'}）", 502
        )


def _is_ipv4(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)

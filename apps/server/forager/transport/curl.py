"""curl 传输层（移植自 libre-tv 的 sources/curl.ts）。

为什么用 curl 子进程而不是 httpx：
上游前置 Cloudflare WAF，判定依据含 TLS ClientHello 指纹与请求头名的原始大小写。
系统 curl 的指纹能过，而 Python 默认 TLS 栈即使带有效 cf_clearance 也会被判 ban。
因此上游请求统一经 curl 发出；头名一律规范化为浏览器形态。

安全：参数全部走 argv 数组，不经 shell；URL 由调用方从常量与已校验 id 构造。
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

MAX_BUFFER = 8 * 1024 * 1024


class CurlError(RuntimeError):
    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class CurlResponse:
    status: int
    headers: dict[str, str]
    body: bytes = b""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")


CANONICAL: dict[str, str] = {
    "user-agent": "User-Agent",
    "accept": "Accept",
    "accept-language": "Accept-Language",
    "accept-encoding": "Accept-Encoding",
    "cookie": "Cookie",
    "referer": "Referer",
    "origin": "Origin",
    "range": "Range",
    "if-range": "If-Range",
    "if-none-match": "If-None-Match",
    "if-modified-since": "If-Modified-Since",
    "content-type": "Content-Type",
}


def canonical_header(name: str) -> str:
    """头名规范化为浏览器形态。Cloudflare 按原始大小写做 WAF 指纹判断。"""
    lower = name.lower()
    if lower in CANONICAL:
        return CANONICAL[lower]
    return "-".join(part[:1].upper() + part[1:] for part in lower.split("-"))


def _common_args(
    *,
    headers: dict[str, str] | None,
    proxy: str | None,
    range_: str | None,
    timeout: float,
    resolve_map: dict[str, str] | None,
    total_timeout: bool = True,
) -> list[str]:
    args = [
        "-sS",
        "-L",
        "--max-redirs",
        "3",
        "--connect-timeout",
        "10",
    ]
    if total_timeout:
        args += ["--max-time", str(int(timeout))]
    else:
        # 媒体流不设总时限：只在下行长期停滞时断开，慢速大文件不冤死
        args += ["--speed-limit", "1024", "--speed-time", "30"]
    if proxy:
        args += ["--proxy", proxy]
    else:
        # 无代理：强制 IPv4 + 用 DoH 结果覆盖解析（本地 DNS 被污染的站点）
        args.append("-4")
        for host, ip in (resolve_map or {}).items():
            args += ["--resolve", f"{host}:443:{ip}"]
    # curl -r 只接受 `start-end`，不接受 HTTP 的 `bytes=start-end` 写法
    if range_:
        args += ["-r", range_.strip().removeprefix("bytes=")]

    merged = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        **(headers or {}),
    }
    for key, value in merged.items():
        if value:
            args += ["-H", f"{canonical_header(key)}: {value}"]
    return args


def parse_headers(raw: str) -> dict[str, str]:
    """重定向会多次出现头块，取最后一个（最终响应）。"""
    blocks = [b for b in raw.split("\r\n\r\n") if b.strip()]
    last = blocks[-1] if blocks else raw
    out: dict[str, str] = {}
    for line in last.split("\r\n"):
        idx = line.find(":")
        if idx > 0 and not line.startswith("HTTP/"):
            out[line[:idx].strip().lower()] = line[idx + 1 :].strip()
    return out


async def curl_request(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    proxy: str | None = None,
    range_: str | None = None,
    retries: int = 0,
    timeout: float = 30,
    resolve_map: dict[str, str] | None = None,
) -> CurlResponse:
    """一次性请求（HTML / 图片）：响应体读入内存后返回。"""
    attempt = 0
    while True:
        try:
            return await _run_once(
                url, headers=headers, proxy=proxy, range_=range_,
                timeout=timeout, resolve_map=resolve_map,
            )
        except CurlError:
            if attempt >= retries:
                raise
            attempt += 1


async def _run_once(
    url: str,
    *,
    headers: dict[str, str] | None,
    proxy: str | None,
    range_: str | None,
    timeout: float,
    resolve_map: dict[str, str] | None,
) -> CurlResponse:
    workdir = Path(tempfile.mkdtemp(prefix="forager-"))
    header_file = workdir / "headers.txt"
    body_file = workdir / "body.bin"
    args = _common_args(
        headers=headers, proxy=proxy, range_=range_,
        timeout=timeout, resolve_map=resolve_map,
    )
    args += ["-D", str(header_file), "-o", str(body_file), "-w", "%{http_code}", url]

    try:
        proc = await asyncio.create_subprocess_exec(
            "curl",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        status = int(out.decode().strip() or 0)
        raw_headers = header_file.read_text("utf-8", errors="replace") if header_file.exists() else ""
        resp_headers = parse_headers(raw_headers)
        body = body_file.read_bytes() if body_file.exists() else b""
        return CurlResponse(status=status, headers=resp_headers, body=body)
    except FileNotFoundError as exc:  # 容器里没装 curl
        raise CurlError("未找到 curl 可执行文件（镜像需预装 curl）", 502) from exc
    except (OSError, ValueError) as exc:
        raise CurlError(f"curl 执行失败：{str(exc)[:140]}", 502) from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@dataclass
class StreamResponse:
    """流式响应：头已就绪，body 由 iter_chunks 逐块吐出。"""

    status: int
    headers: dict[str, str]
    _proc: asyncio.subprocess.Process
    _workdir: Path
    _closed: bool = field(default=False, repr=False)

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")

    @property
    def content_length(self) -> int | None:
        raw = self.headers.get("content-length")
        return int(raw) if raw and raw.isdigit() else None

    async def iter_chunks(self, size: int = 65536) -> AsyncIterator[bytes]:
        assert self._proc.stdout is not None
        try:
            while True:
                chunk = await self._proc.stdout.read(size)
                if not chunk:
                    break
                yield chunk
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._proc.kill()
        with contextlib.suppress(Exception):
            await self._proc.wait()
        shutil.rmtree(self._workdir, ignore_errors=True)


async def open_stream(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    proxy: str | None = None,
    range_: str | None = None,
    timeout: float = 60,
    resolve_map: dict[str, str] | None = None,
) -> StreamResponse:
    """流式请求：头解析完成后返回，body 仍在上游 → 客户端。

    curl 用 `-D <file> -o -`：头先落文件、体走 stdout。头块写完即可流式转发，
    不必等整个响应落地（大视频也不能等）。
    """
    workdir = Path(tempfile.mkdtemp(prefix="forager-"))
    header_file = workdir / "headers.txt"
    args = _common_args(
        headers=headers, proxy=proxy, range_=range_,
        timeout=timeout, resolve_map=resolve_map, total_timeout=False,
    )
    args += ["-D", str(header_file), "-o", "-", url]

    try:
        proc = await asyncio.create_subprocess_exec(
            "curl",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        raise CurlError("未找到 curl 可执行文件（镜像需预装 curl）", 502) from exc

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 15
    status, resp_headers = 0, {}
    while True:
        raw = b""
        with contextlib.suppress(FileNotFoundError):
            raw = header_file.read_bytes()
        if raw.endswith(b"\r\n\r\n"):
            blocks = [b for b in raw.split(b"\r\n\r\n") if b.strip()]
            if blocks:
                status, resp_headers = _parse_block(blocks[-1])
                # 重定向：继续等下一块（-L 会自行跟随）
                if status not in {301, 302, 303, 307, 308}:
                    break
        if proc.returncode is not None:
            with contextlib.suppress(Exception):
                await proc.wait()
            shutil.rmtree(workdir, ignore_errors=True)
            raise CurlError("上游连接失败：curl 未返回响应头", 502)
        if loop.time() > deadline:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            shutil.rmtree(workdir, ignore_errors=True)
            raise CurlError("上游响应头超时", 504)
        await asyncio.sleep(0.02)

    return StreamResponse(status=status, headers=resp_headers, _proc=proc, _workdir=workdir)


def _parse_block(block: bytes) -> tuple[int, dict[str, str]]:
    lines = block.decode("utf-8", errors="replace").split("\r\n")
    status = 0
    if lines and lines[0].startswith("HTTP/"):
        parts = lines[0].split()
        if len(parts) >= 2 and parts[1].isdigit():
            status = int(parts[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        idx = line.find(":")
        if idx > 0:
            headers[line[:idx].strip().lower()] = line[idx + 1 :].strip()
    return status, headers

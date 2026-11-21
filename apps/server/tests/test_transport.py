"""传输层参数：媒体流不能设总时限，否则慢速大文件会被误杀。"""

from __future__ import annotations

from forager.transport.curl import _common_args


def test_media_stream_uses_stall_timeout() -> None:
    args = _common_args(
        headers=None,
        proxy=None,
        range_=None,
        timeout=60,
        resolve_map=None,
        total_timeout=False,
    )
    assert "--max-time" not in args
    assert "--speed-limit" in args
    assert "--speed-time" in args


def test_plain_request_keeps_total_timeout() -> None:
    args = _common_args(
        headers=None,
        proxy=None,
        range_=None,
        timeout=30,
        resolve_map=None,
    )
    assert args[args.index("--max-time") + 1] == "30"
    assert "--speed-time" not in args

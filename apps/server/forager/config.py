"""运行时配置：全部来自环境变量，与旧版 libre-tv 的变量名兼容。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# forager/config.py → parents[0]=forager, [1]=apps/server, [2]=apps, [3]=仓库根
_SERVER_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _SERVER_ROOT.parents[1]


def _env(*names: str, default: str | None = None) -> str | None:
    """按顺序取第一个非空环境变量（旧名优先，便于沿用 .env）。"""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    port: int
    data_dir: Path
    static_dir: Path
    # hanime1 上游
    hanime1_base_url: str
    hanime1_proxy: str | None
    hanime1_dns_proxy: str | None
    hanime1_pinned_ip: str | None
    hanime1_cookie: str | None
    # 采集策略
    min_interval_ms: int
    html_cache_ttl_ms: int
    download_concurrency: int
    sub_tick_sec: int
    sub_initial: int
    # pixiv 上游
    pixiv_proxy: str | None = None
    pixiv_cookie: str | None = None
    # 凭据加密密钥（Fernet，urlsafe base64）；缺省时自动生成并落盘
    secret_key: str | None = None


def load_settings() -> Settings:
    data_dir = Path(_env("DATA_DIR", "FORAGER_DATA_DIR", default=str(_REPO_ROOT / "data"))).expanduser()
    static_dir = Path(
        _env("FORAGER_STATIC_DIR", default=str(_SERVER_ROOT / "static"))
    ).expanduser()
    return Settings(
        port=int(_env("PORT", "FORAGER_PORT", default="11408") or 11408),
        data_dir=data_dir,
        static_dir=static_dir,
        hanime1_base_url=_env("HANIME1_BASE_URL", default="https://hanime1.me") or "https://hanime1.me",
        hanime1_proxy=_env("HANIME1_PROXY"),
        hanime1_dns_proxy=_env("HANIME1_DNS_PROXY"),
        hanime1_pinned_ip=_env("HANIME1_RESOLVE"),
        hanime1_cookie=_env("HANIME1_COOKIE"),
        pixiv_proxy=_env("PIXIV_PROXY"),
        pixiv_cookie=_env("PIXIV_COOKIE"),
        min_interval_ms=int(_env("FORAGER_MIN_INTERVAL_MS", default="400") or 400),
        html_cache_ttl_ms=int(_env("FORAGER_CACHE_TTL_MS", default="60000") or 60000),
        download_concurrency=int(_env("FORAGER_DOWNLOAD_CONCURRENCY", default="1") or 1),
        sub_tick_sec=int(_env("FORAGER_SUB_TICK", default="30") or 30),
        sub_initial=int(_env("FORAGER_SUB_INITIAL", default="3") or 3),
        secret_key=_env("FORAGER_SECRET_KEY"),
    )

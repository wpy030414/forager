"""凭据加密：Cookie 等敏感值不落明文。

密钥优先级：环境变量 FORAGER_SECRET_KEY → 数据目录下 .secret_key（首次自动生成，0600）。
两者都没有也能跑（自动生成），但迁移数据目录会解不开旧凭据——换机器时请显式指定。
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "enc:v1:"


class SecretBox:
    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, text: str) -> str:
        return PREFIX + self._fernet.encrypt(text.encode()).decode()

    def decrypt(self, blob: str) -> str:
        if not blob.startswith(PREFIX):
            return blob  # 兼容历史明文
        try:
            return self._fernet.decrypt(blob[len(PREFIX) :].encode()).decode()
        except InvalidToken as exc:
            raise ValueError("凭据解密失败（密钥已更换？请在设置页重新填写）") from exc


def load_key(data_dir: Path, env_key: str | None = None) -> bytes:
    if env_key:
        return env_key.encode()
    path = Path(data_dir) / ".secret_key"
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    os.chmod(path, 0o600)
    return key


def mask(value: str) -> str:
    """只回显尾部 4 位，前端据此判断「已配置」。"""
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "****"
    return "****" + text[-4:]

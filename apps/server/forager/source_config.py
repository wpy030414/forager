"""源配置：把 Cookie / 代理 / 请求间隔从环境变量搬到可热更新的存储。

- 存 settings 表，键为 `source.<id>.<field>`
- cookie 等敏感字段加密后落库
- 保存后立即应用到内存中的 client，无需重启容器
"""

from __future__ import annotations

from typing import Any

from .db import Database
from .secrets import SecretBox, mask
from .sources.base import BaseSource

#: 需要加密存储的字段
SECRET_FIELDS = frozenset({"cookie"})

#: 前端渲染用的字段元数据
FIELD_META: dict[str, dict[str, str]] = {
    "cookie": {
        "label": "Cookie",
        "type": "secret",
        "placeholder": "PHPSESSID=...",
        "help": "浏览器登录源站后从 DevTools 复制整串 Cookie；留空表示保持不变",
    },
    "proxy": {
        "label": "上游代理",
        "type": "text",
        "placeholder": "http://127.0.0.1:7897",
        "help": "国内直连被拦截 / 海外访问时使用",
    },
    "dns_proxy": {
        "label": "DoH 解析代理",
        "type": "text",
        "placeholder": "http://127.0.0.1:7897",
        "help": "仅解析走代理，站点访问仍直连",
    },
    "pinned_ip": {
        "label": "钉死 IPv4",
        "type": "text",
        "placeholder": "104.26.8.104",
        "help": "跳过 DoH，直接用该 IP 访问源站",
    },
    "quality": {
        "label": "默认清晰度",
        "type": "select",
        "placeholder": "",
        "help": "线索未单独指定时使用；「跟随默认」表示取源站最优",
    },
    "min_interval_ms": {
        "label": "请求间隔（毫秒）",
        "type": "number",
        "placeholder": "1200",
        "help": "同源两次请求的最小间隔；太小会触发风控",
    },
}


class SourceConfigStore:
    def __init__(self, db: Database, box: SecretBox) -> None:
        self.db = db
        self.box = box

    @staticmethod
    def _key(source_id: str, field: str) -> str:
        return f"source.{source_id}.{field}"

    # ---- 读写 ----

    def values(self, source_id: str, fields: tuple[str, ...]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for field in fields:
            raw = self.db.get_setting(self._key(source_id, field))
            if raw is None:
                continue
            if field in SECRET_FIELDS:
                try:
                    out[field] = self.box.decrypt(raw)
                except ValueError:
                    continue  # 解密失败就当没配置，避免整个源不可用
            elif field == "min_interval_ms":
                out[field] = int(raw)
            else:
                out[field] = raw
        return out

    def save(self, source_id: str, fields: tuple[str, ...], payload: dict[str, Any]) -> None:
        """空字符串 / null 表示清除该字段；缺省表示保持不变。"""
        for field in fields:
            if field not in payload:
                continue
            value = payload.get(field)
            key = self._key(source_id, field)
            if value is None or value == "":
                self.db.execute("DELETE FROM settings WHERE key = ?", (key,))
                continue
            text = str(value).strip()
            if field in SECRET_FIELDS:
                text = self.box.encrypt(text)
            self.db.set_setting(key, text)

    # ---- 应用与展示 ----

    def apply_all(self, sources: dict[str, BaseSource]) -> None:
        """环境变量默认值打底、设置页保存值覆盖。

        合并后每个字段都在字典里，所以某字段被清空时会回到默认值，
        而不是让内存里的 client 继续用旧值。
        """
        for source_id, source in sources.items():
            stored = self.values(source_id, source.config_fields)
            source.apply_config({**source.env_config, **stored})

    def describe(self, sources: dict[str, BaseSource]) -> list[dict[str, Any]]:
        result = []
        for source_id, source in sources.items():
            if not source.config_fields:
                continue  # 本地源等无可配置项的源不出现在设置页
            stored = self.values(source_id, source.config_fields)
            fields = []
            for field in source.config_fields:
                meta = FIELD_META.get(field, {"label": field, "type": "text"})
                item: dict[str, Any] = {"key": field, **meta}
                if field == "quality":
                    item["type"] = "select"
                    item["options"] = [{"value": "", "label": "跟随默认"}] + [
                        {"value": q, "label": q} for q in source.quality_options
                    ]
                value = stored.get(field)
                if field in SECRET_FIELDS:
                    item["configured"] = bool(value)
                    item["hint"] = mask(str(value)) if value else ""
                else:
                    item["value"] = value if value is not None else ""
                    if field == "proxy" and value is None and source.env_config.get("proxy"):
                        # 留空即跟随全局出口；把生效中的地址显示出来
                        item["placeholder"] = source.env_config["proxy"]
                fields.append(item)
            result.append(
                {
                    "source": source_id,
                    "name": source.name,
                    "canQrcode": bool(source.supports_qrcode),
                    "fields": fields,
                }
            )
        return result

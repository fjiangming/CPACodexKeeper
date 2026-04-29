"""
.env 文件读写管理器。
读取和更新 .env 配置，保留注释和格式。
"""

import os
from pathlib import Path

# 全部 11 个原始配置项的元信息
CONFIG_META = [
    {
        "key": "CPA_ENDPOINT",
        "label": "CPA API 地址",
        "description": "CPA 管理 API 基础地址，必须以 http:// 或 https:// 开头",
        "type": "string",
        "required": True,
        "default": "",
        "sensitive": False,
    },
    {
        "key": "CPA_TOKEN",
        "label": "管理 Token",
        "description": "CPA 管理端的 Bearer Token 凭证",
        "type": "string",
        "required": True,
        "default": "",
        "sensitive": True,
    },
    {
        "key": "CPA_PROXY",
        "label": "代理地址",
        "description": "可选 HTTP/HTTPS 代理，例如 http://127.0.0.1:7890",
        "type": "string",
        "required": False,
        "default": "",
        "sensitive": False,
    },
    {
        "key": "CPA_INTERVAL",
        "label": "轮询间隔（秒）",
        "description": "守护模式下每轮巡检的间隔时间",
        "type": "int",
        "required": False,
        "default": "1800",
        "sensitive": False,
        "min": 1,
    },
    {
        "key": "CPA_QUOTA_THRESHOLD",
        "label": "禁用阈值（%）",
        "description": "额度使用达到此百分比时禁用 Token（0-100）",
        "type": "int",
        "required": False,
        "default": "100",
        "sensitive": False,
        "min": 0,
        "max": 100,
    },
    {
        "key": "CPA_EXPIRY_THRESHOLD_DAYS",
        "label": "刷新阈值（天）",
        "description": "已禁用 Token 剩余有效期低于此天数时触发刷新",
        "type": "int",
        "required": False,
        "default": "3",
        "sensitive": False,
        "min": 0,
    },
    {
        "key": "CPA_ENABLE_REFRESH",
        "label": "自动刷新",
        "description": "是否启用对已禁用 Token 的自动刷新（启用状态 Token 交给 CPA 处理）",
        "type": "bool",
        "required": False,
        "default": "true",
        "sensitive": False,
    },
    {
        "key": "CPA_HTTP_TIMEOUT",
        "label": "CPA 请求超时（秒）",
        "description": "CPA Management API 请求超时时间",
        "type": "int",
        "required": False,
        "default": "30",
        "sensitive": False,
        "min": 1,
    },
    {
        "key": "CPA_USAGE_TIMEOUT",
        "label": "Usage 请求超时（秒）",
        "description": "OpenAI Usage API 请求超时时间",
        "type": "int",
        "required": False,
        "default": "15",
        "sensitive": False,
        "min": 1,
    },
    {
        "key": "CPA_MAX_RETRIES",
        "label": "重试次数",
        "description": "临时网络或 5xx 服务端错误的重试次数（0-5）",
        "type": "int",
        "required": False,
        "default": "2",
        "sensitive": False,
        "min": 0,
        "max": 5,
    },
    {
        "key": "CPA_WORKER_THREADS",
        "label": "并发线程数",
        "description": "单轮巡检中同时处理 Token 的线程数",
        "type": "int",
        "required": False,
        "default": "8",
        "sensitive": False,
        "min": 1,
    },
]


class ConfigManager:
    """读写 .env 文件，保留注释和格式。"""

    def __init__(self, env_path: str | Path | None = None):
        if env_path is None:
            env_path = Path(__file__).resolve().parents[1] / ".env"
        self.env_path = Path(env_path)

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def _parse_env_file(self) -> dict[str, str]:
        """解析 .env 文件，返回 {key: value}。"""
        if not self.env_path.exists():
            return {}
        values: dict[str, str] = {}
        for raw_line in self.env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            values[key] = value
        return values

    def read_all(self) -> dict[str, str]:
        """读取所有配置项的原始值。"""
        values = self._parse_env_file()
        for meta in CONFIG_META:
            env_value = os.getenv(meta["key"])
            if env_value not in (None, ""):
                values[meta["key"]] = env_value
        return values

    def read_with_meta(self) -> list[dict]:
        """读取配置项，附带元信息和当前值。"""
        raw = self._parse_env_file()
        result = []
        for meta in CONFIG_META:
            item = {**meta}
            env_value = os.getenv(meta["key"])
            if env_value not in (None, ""):
                item["value"] = env_value
                item["source"] = "environment"
            elif meta["key"] in raw:
                item["value"] = raw[meta["key"]]
                item["source"] = "file"
            else:
                item["value"] = meta["default"]
                item["source"] = "default"
            result.append(item)
        return result

    def get(self, key: str, default: str = "") -> str:
        """读取单个配置项。"""
        return self.read_all().get(key, default)

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------

    def validate(self, key: str, value: str) -> str | None:
        """校验单个配置项，返回错误信息或 None。"""
        meta = next((m for m in CONFIG_META if m["key"] == key), None)
        if meta is None:
            return f"未知配置项: {key}"

        if meta.get("required") and not value.strip():
            return f"{meta['label']} 不能为空"

        if meta["type"] == "int" and value.strip():
            try:
                v = int(value)
            except ValueError:
                return f"{meta['label']} 必须是整数"
            if "min" in meta and v < meta["min"]:
                return f"{meta['label']} 不能小于 {meta['min']}"
            if "max" in meta and v > meta["max"]:
                return f"{meta['label']} 不能大于 {meta['max']}"

        if meta["type"] == "bool" and value.strip():
            if value.strip().lower() not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
                return f"{meta['label']} 必须是布尔值"

        if key == "CPA_ENDPOINT" and value.strip():
            if not value.strip().startswith(("http://", "https://")):
                return "必须以 http:// 或 https:// 开头"

        return None

    def validate_all(self, changes: dict[str, str]) -> list[str]:
        """校验多个配置项，返回错误列表。"""
        errors = []
        for key, value in changes.items():
            err = self.validate(key, value)
            if err:
                errors.append(f"{key}: {err}")
        return errors

    # ------------------------------------------------------------------
    # 更新
    # ------------------------------------------------------------------

    def update(self, changes: dict[str, str]) -> None:
        """更新配置项，保留 .env 文件中的注释和未修改行。"""
        if not changes:
            return

        remaining = dict(changes)

        if self.env_path.exists():
            lines = self.env_path.read_text(encoding="utf-8").splitlines()
        else:
            lines = []

        new_lines: list[str] = []
        for raw_line in lines:
            stripped = raw_line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in remaining:
                    new_lines.append(f"{key}={remaining.pop(key)}")
                    continue
            new_lines.append(raw_line)

        # 追加不在文件中的新 key
        for key, value in remaining.items():
            new_lines.append(f"{key}={value}")

        self.env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

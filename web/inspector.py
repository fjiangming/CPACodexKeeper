"""
巡检调度器。
两种数据获取模式：直接调 API 刷新 / 子进程执行 main.py --once。
"""

import asyncio
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .cpa_api import CPAApi
from .openai_api import OpenAIApi
from .store import DataStore


TOKEN_HEADER_RE = re.compile(r"^\[(\d+)/(\d+)\]\s+(.+?)\s*$")


def _format_seconds(seconds: float) -> str:
    """将秒数格式化为人类可读字符串。"""
    if seconds < 0:
        return "已过期"
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    if days > 0:
        return f"{days}天{hours}小时"
    if hours > 0:
        return f"{hours}小时{minutes}分钟"
    return f"{minutes}分钟"


def _parse_expired_time(expired_str: str) -> float:
    """解析过期时间字符串，返回剩余秒数。"""
    if not expired_str:
        return -1
    try:
        expired_str = expired_str.strip()
        if "T" in expired_str:
            if expired_str.endswith("Z"):
                expired_str = expired_str[:-1] + "+00:00"
            for fmt in ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"]:
                try:
                    dt = datetime.strptime(expired_str, fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return (dt - datetime.now(timezone.utc)).total_seconds()
                except ValueError:
                    continue
        return -1
    except Exception:
        return -1


def _format_window_label(seconds: int | None) -> str:
    """格式化窗口标签。"""
    if seconds == 18000:
        return "5h"
    if seconds == 604800:
        return "Week"
    if seconds is None:
        return "-"
    hours = seconds // 3600
    if hours > 0:
        return f"{hours}h"
    return f"{seconds // 60}m"


def _read_int(config: dict, key: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    raw = config.get(key, "")
    if raw in (None, ""):
        return default
    value = int(raw)
    if value < minimum:
        return minimum
    if maximum is not None and value > maximum:
        return maximum
    return value


async def _gather_limited(coroutines: list, limit: int) -> list:
    if not coroutines:
        return []

    semaphore = asyncio.Semaphore(limit)

    async def guarded(coro):
        async with semaphore:
            return await coro

    return await asyncio.gather(*(guarded(coro) for coro in coroutines), return_exceptions=True)


def _empty_action_summary() -> dict[str, list[dict[str, str]]]:
    return {
        "deleted": [],
        "disabled": [],
        "enabled": [],
        "refreshed": [],
        "planned_deleted": [],
        "planned_disabled": [],
        "planned_enabled": [],
        "planned_refreshed": [],
    }


def _add_action(actions: dict[str, list[dict[str, str]]], key: str, name: str, email: str = "") -> None:
    if not name:
        return
    item = {"name": name, "email": email or ""}
    if item not in actions[key]:
        actions[key].append(item)


def extract_inspection_actions(output: str, *, dry_run: bool = False) -> dict[str, list[dict[str, str]]]:
    """Extract token actions from CLI inspection output."""
    actions = _empty_action_summary()
    current_name = ""
    current_email = ""

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        header_match = TOKEN_HEADER_RE.match(line)
        if header_match:
            current_name = header_match.group(3).strip()
            current_email = ""
            continue

        if line.startswith("[*] Email:"):
            current_email = line.split("Email:", 1)[1].strip()
            continue

        if "[DRY-RUN]" in line:
            if "将删除:" in line:
                name = line.rsplit(":", 1)[-1].strip() or current_name
                _add_action(actions, "planned_deleted", name, current_email)
            elif "将禁用:" in line:
                name = line.rsplit(":", 1)[-1].strip() or current_name
                _add_action(actions, "planned_disabled", name, current_email)
            elif "将启用:" in line:
                name = line.rsplit(":", 1)[-1].strip() or current_name
                _add_action(actions, "planned_enabled", name, current_email)
            elif "将上传更新" in line:
                name = line.rsplit(" ", 1)[-1].strip() or current_name
                _add_action(actions, "planned_refreshed", name, current_email)
            continue

        if dry_run:
            continue

        if line.startswith("[DELETE]"):
            _add_action(actions, "deleted", current_name, current_email)
        elif line.startswith("[DISABLED]") and "刷新后保持禁用" not in line:
            _add_action(actions, "disabled", current_name, current_email)
        elif line.startswith("[ENABLED]"):
            _add_action(actions, "enabled", current_name, current_email)
        elif line.startswith("[REFRESH]"):
            _add_action(actions, "refreshed", current_name, current_email)

    return actions


class Inspector:
    """负责获取 Token 数据和触发巡检。"""

    def __init__(self, project_root: str | Path | None = None):
        if project_root is None:
            project_root = Path(__file__).resolve().parents[1]
        self.project_root = Path(project_root)
        self._refresh_lock = asyncio.Lock()
        self._inspect_lock = asyncio.Lock()

    async def refresh_tokens(self, store: DataStore, config: dict) -> None:
        """直接调用 CPA + OpenAI API 获取最新 Token 状态。"""
        async with self._refresh_lock:
            await self._refresh_tokens_locked(store, config)

    async def _refresh_tokens_locked(self, store: DataStore, config: dict) -> None:
        endpoint = config.get("CPA_ENDPOINT", "").strip().rstrip("/")
        token = config.get("CPA_TOKEN", "").strip()
        proxy = config.get("CPA_PROXY", "").strip() or None
        cpa_timeout = _read_int(config, "CPA_HTTP_TIMEOUT", 30, minimum=1)
        usage_timeout = _read_int(config, "CPA_USAGE_TIMEOUT", 15, minimum=1)
        threshold = _read_int(config, "CPA_QUOTA_THRESHOLD", 100, minimum=0, maximum=100)
        max_retries = _read_int(config, "CPA_MAX_RETRIES", 2, minimum=0, maximum=5)
        worker_threads = _read_int(config, "CPA_WORKER_THREADS", 8, minimum=1)

        if not endpoint or not token:
            return

        store.set_refreshing(True)
        cpa = CPAApi(endpoint, token, proxy=proxy, timeout=cpa_timeout, max_retries=max_retries)
        openai = OpenAIApi(proxy=proxy, timeout=usage_timeout, max_retries=max_retries)

        try:
            # 1. 获取 token 列表
            token_list = await cpa.list_tokens()
            if not token_list:
                store.update_tokens([], {
                    "total": 0, "alive": 0, "dead": 0,
                    "disabled": 0, "quota_full": 0, "has_refresh": 0,
                })
                return

            # 2. 并发获取每个 token 的详情
            detail_tasks = [cpa.get_token_detail(t.get("name", "")) for t in token_list]
            details = await _gather_limited(detail_tasks, worker_threads)

            # 3. 构建带详情的 token 列表，并发检测 usage
            tokens_with_details = []
            for info, detail in zip(token_list, details):
                name = info.get("name", "unknown")
                if isinstance(detail, Exception) or detail is None:
                    tokens_with_details.append({
                        "name": name,
                        "email": "获取失败",
                        "status": "error",
                        "disabled": False,
                        "primary_pct": None,
                        "secondary_pct": None,
                        "plan_type": "-",
                        "has_credits": False,
                        "expired": "-",
                        "remaining": "-",
                        "has_refresh_token": False,
                        "primary_label": "-",
                        "secondary_label": "-",
                        "error": str(detail) if isinstance(detail, Exception) else "获取详情失败",
                    })
                    continue
                tokens_with_details.append({"name": name, "_detail": detail})

            # 4. 并发检测 usage
            usage_tasks = []
            usage_indices = []
            for i, t in enumerate(tokens_with_details):
                if "_detail" not in t:
                    continue
                detail = t["_detail"]
                access_token = detail.get("access_token", "")
                account_id = detail.get("account_id")
                if access_token:
                    usage_tasks.append(openai.check_usage(access_token, account_id))
                    usage_indices.append(i)
                else:
                    # 没有 access_token 的标记为错误
                    t.pop("_detail")
                    t.update({
                        "email": detail.get("email", "unknown"),
                        "status": "error",
                        "disabled": detail.get("disabled", False),
                        "primary_pct": None,
                        "secondary_pct": None,
                        "plan_type": "-",
                        "has_credits": False,
                        "expired": detail.get("expired", "-"),
                        "remaining": "-",
                        "has_refresh_token": bool((detail.get("refresh_token") or "").strip()),
                        "primary_label": "-",
                        "secondary_label": "-",
                        "error": "缺少 access_token",
                    })

            usage_results = await _gather_limited(usage_tasks, worker_threads)

            # 5. 组装最终数据
            for idx, usage in zip(usage_indices, usage_results):
                t = tokens_with_details[idx]
                detail = t.pop("_detail")
                email = detail.get("email", "unknown")
                disabled = detail.get("disabled", False)
                expired_str = detail.get("expired", "")
                remaining_seconds = _parse_expired_time(expired_str)
                remaining_str = _format_seconds(remaining_seconds) if remaining_seconds != -1 else "未知"
                has_rt = bool((detail.get("refresh_token") or "").strip())

                if isinstance(usage, Exception):
                    t.update({
                        "email": email,
                        "status": "error",
                        "disabled": disabled,
                        "primary_pct": None,
                        "secondary_pct": None,
                        "plan_type": "-",
                        "has_credits": False,
                        "expired": expired_str or "-",
                        "remaining": remaining_str,
                        "has_refresh_token": has_rt,
                        "primary_label": "-",
                        "secondary_label": "-",
                        "error": str(usage),
                    })
                    continue

                status_code = usage.get("status_code")
                if status_code in (401, 402):
                    status = "dead"
                elif status_code != 200:
                    status = "error"
                elif disabled:
                    status = "disabled"
                else:
                    # 检查额度
                    p_pct = usage.get("primary_pct", 0)
                    s_pct = usage.get("secondary_pct")
                    if p_pct >= threshold or (s_pct is not None and s_pct >= threshold):
                        status = "quota_full"
                    else:
                        status = "alive"

                t.update({
                    "email": email,
                    "status": status,
                    "disabled": disabled,
                    "primary_pct": usage.get("primary_pct"),
                    "secondary_pct": usage.get("secondary_pct"),
                    "plan_type": usage.get("plan_type", "-"),
                    "has_credits": usage.get("has_credits", False),
                    "expired": expired_str or "-",
                    "remaining": remaining_str,
                    "has_refresh_token": has_rt,
                    "primary_label": _format_window_label(usage.get("primary_window_seconds")),
                    "secondary_label": _format_window_label(usage.get("secondary_window_seconds")),
                    "error": usage.get("error"),
                })

            # 清理残留的 _detail
            for t in tokens_with_details:
                t.pop("_detail", None)

            # 6. 计算统计
            stats = {
                "total": len(tokens_with_details),
                "alive": sum(1 for t in tokens_with_details if t["status"] == "alive"),
                "dead": sum(1 for t in tokens_with_details if t["status"] == "dead"),
                "disabled": sum(1 for t in tokens_with_details if t.get("disabled")),
                "quota_full": sum(1 for t in tokens_with_details if t["status"] == "quota_full"),
                "has_refresh": sum(1 for t in tokens_with_details if t.get("has_refresh_token")),
            }

            store.update_tokens(tokens_with_details, stats)

        finally:
            await cpa.close()
            await openai.close()
            store.set_refreshing(False)

    async def run_inspection(self, store: DataStore, config: dict, *, dry_run: bool = False, source: str = "manual") -> str:
        """调用 main.py --once 子进程执行完整巡检。"""
        main_py = self.project_root / "main.py"
        if not main_py.exists():
            return "main.py not found"

        async with self._inspect_lock:
            args = [sys.executable, str(main_py), "--once"]
            if dry_run:
                args.append("--dry-run")

            proc = None
            store.set_inspecting(True)
            try:
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(self.project_root),
                )
                stdout, _ = await proc.communicate()
                output = stdout.decode("utf-8", errors="replace") if stdout else ""
                actions = extract_inspection_actions(output, dry_run=dry_run)

                # 记录历史
                store.add_history({
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "exit_code": proc.returncode,
                    "output_lines": len(output.splitlines()),
                    "stats": store.get_stats(),
                    "dry_run": dry_run,
                    "source": source,
                    "actions": actions,
                    "action_counts": {key: len(value) for key, value in actions.items()},
                    "deleted_tokens": actions["planned_deleted"] if dry_run else actions["deleted"],
                    "disabled_tokens": actions["planned_disabled"] if dry_run else actions["disabled"],
                })

                store.set_inspect_done(output)

                # 巡检完成后自动刷新 token 数据
                await self.refresh_tokens(store, config)

                return output
            except asyncio.CancelledError:
                if proc is not None and proc.returncode is None:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5)
                    except Exception:
                        proc.kill()
                store.set_inspect_done("inspection cancelled")
                raise
            except Exception as exc:
                store.set_inspect_done(str(exc))
                return str(exc)

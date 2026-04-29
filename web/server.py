"""
CPACodexKeeper Web 监控仪表盘 - FastAPI 主入口。
"""

import asyncio
import os
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config_manager import ConfigManager
from .inspector import Inspector
from .store import DataStore

# ------------------------------------------------------------------
# 初始化
# ------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"

config_mgr = ConfigManager(PROJECT_ROOT / ".env")
store = DataStore()
inspector = Inspector(PROJECT_ROOT)


def _read_interval_seconds(config: dict) -> int:
    raw = config.get("CPA_INTERVAL", "1800") or "1800"
    try:
        return max(1, int(raw))
    except ValueError:
        return 1800


def _has_required_config(config: dict) -> bool:
    return bool(config.get("CPA_ENDPOINT", "").strip() and config.get("CPA_TOKEN", "").strip())


def _read_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


async def _daemon_loop():
    while True:
        config = config_mgr.read_all()
        interval_seconds = _read_interval_seconds(config)
        next_run = time.time() + interval_seconds
        store.set_daemon_state(interval_seconds, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(next_run)))

        await asyncio.sleep(interval_seconds)

        config = config_mgr.read_all()
        if not _has_required_config(config):
            continue

        status = store.get_status()
        if status["is_inspecting"] or status["is_refreshing"]:
            continue

        await inspector.run_inspection(store, config, source="daemon")


@asynccontextmanager
async def lifespan(application: FastAPI):
    daemon_task = asyncio.create_task(_daemon_loop())

    # 启动时自动刷新一次数据
    config = config_mgr.read_all()
    if _has_required_config(config):
        try:
            await inspector.refresh_tokens(store, config)
        except Exception:
            pass
    try:
        yield
    finally:
        daemon_task.cancel()
        with suppress(asyncio.CancelledError):
            await daemon_task


app = FastAPI(title="CPACodexKeeper Dashboard", docs_url=None, redoc_url=None, lifespan=lifespan)


# ------------------------------------------------------------------
# 页面
# ------------------------------------------------------------------

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


# ------------------------------------------------------------------
# 数据 API
# ------------------------------------------------------------------

@app.get("/api/status")
async def api_status():
    """返回当前运行状态和统计。"""
    return store.get_status()


@app.get("/api/tokens")
async def api_tokens():
    """返回 Token 列表。"""
    return store.get_tokens()


@app.get("/api/history")
async def api_history():
    """返回巡检历史记录。"""
    return store.get_history()


# ------------------------------------------------------------------
# 配置 API
# ------------------------------------------------------------------

@app.get("/api/config")
async def api_config_get():
    """返回全部配置项（含当前值和元信息）。"""
    items = config_mgr.read_with_meta()
    # 脱敏处理
    for item in items:
        if item.get("sensitive") and item.get("value"):
            v = item["value"]
            if len(v) > 8:
                item["display_value"] = v[:4] + "•" * (len(v) - 8) + v[-4:]
            else:
                item["display_value"] = "•" * len(v)
        else:
            item["display_value"] = item.get("value", "")
    return items


@app.post("/api/config")
async def api_config_update(request: Request):
    """更新配置项，写回 .env 文件。"""
    body = await request.json()
    changes = body.get("changes", {})
    if not changes:
        return JSONResponse({"ok": False, "error": "没有提交修改"}, status_code=400)

    errors = config_mgr.validate_all(changes)
    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=400)

    config_mgr.update(changes)
    return {"ok": True, "message": "配置已保存"}


# ------------------------------------------------------------------
# 操作 API
# ------------------------------------------------------------------

@app.post("/api/refresh")
async def api_refresh():
    """手动刷新 Token 数据（直接调 CPA + OpenAI API）。"""
    status = store.get_status()
    if status["is_refreshing"]:
        return JSONResponse({"ok": False, "error": "正在刷新中"}, status_code=409)
    if status["is_inspecting"]:
        return JSONResponse({"ok": False, "error": "正在巡检中"}, status_code=409)

    config = config_mgr.read_all()
    await inspector.refresh_tokens(store, config)
    return {"ok": True, "message": "刷新完成"}


@app.post("/api/inspect")
async def api_inspect(request: Request):
    """手动触发巡检（调用 main.py --once）。"""
    status = store.get_status()
    if status["is_inspecting"]:
        return JSONResponse({"ok": False, "error": "正在巡检中"}, status_code=409)
    if status["is_refreshing"]:
        return JSONResponse({"ok": False, "error": "正在刷新中"}, status_code=409)

    body = {}
    if request.headers.get("content-type", "").lower().startswith("application/json"):
        try:
            body = await request.json()
        except Exception:
            body = {}
    dry_run = _read_bool(body.get("dry_run", False))

    config = config_mgr.read_all()
    output = await inspector.run_inspection(store, config, dry_run=dry_run, source="manual")
    return {"ok": True, "message": "巡检完成", "output": output}


@app.get("/api/inspect/output")
async def api_inspect_output():
    """获取最近一次巡检的输出。"""
    return {"output": store.inspect_output}


# ------------------------------------------------------------------
# 入口
# ------------------------------------------------------------------

def main():
    import uvicorn

    host = os.getenv("CPA_WEB_HOST", "0.0.0.0")
    port = int(os.getenv("CPA_WEB_PORT", "8377"))
    print(f"CPACodexKeeper Dashboard starting on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

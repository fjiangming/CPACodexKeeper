"""
CPACodexKeeper Web 监控仪表盘 - FastAPI 主入口。
"""

import asyncio
import hashlib
import hmac
import os
import secrets
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
# 认证
# ------------------------------------------------------------------

# 每次进程启动生成一个随机密钥，重启后旧 session 自动失效
_SECRET_KEY = secrets.token_bytes(32)
# 存活的 session token 集合
_sessions: set[str] = set()

# 白名单路由（不需要认证）
_AUTH_WHITELIST = {"/api/auth/login", "/favicon.ico"}


def _get_password() -> str:
    """读取 Web 访问密码（优先环境变量，其次 .env 文件）。"""
    env_val = os.getenv("CPA_WEB_PASSWORD", "").strip()
    if env_val:
        return env_val
    return config_mgr.get("CPA_WEB_PASSWORD", "").strip()


def _sign_token(token: str) -> str:
    """对 session token 做 HMAC-SHA256 签名。"""
    return hmac.new(_SECRET_KEY, token.encode(), hashlib.sha256).hexdigest()


def _make_cookie_value(token: str) -> str:
    """生成 Cookie 值 = token.signature"""
    return f"{token}.{_sign_token(token)}"


def _verify_cookie(cookie_value: str) -> bool:
    """验证 Cookie 值的签名和 session 有效性。"""
    if not cookie_value or "." not in cookie_value:
        return False
    token, sig = cookie_value.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign_token(token)):
        return False
    return token in _sessions


_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter','Segoe UI',sans-serif;background:#0f1117;color:#e4e6ed;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#1a1d27;border:1px solid #2d3348;border-radius:16px;padding:40px;width:360px;max-width:90vw}
.card h2{font-size:18px;margin-bottom:24px;text-align:center;color:#8b8fa3}
.field{margin-bottom:20px}
.field input{width:100%;padding:12px 14px;background:#0f1117;border:1px solid #2d3348;border-radius:8px;color:#e4e6ed;font-size:14px;outline:none;transition:border-color .2s}
.field input:focus{border-color:#6c5ce7}
.btn{width:100%;padding:12px;border:none;border-radius:8px;background:#6c5ce7;color:#fff;font-size:14px;font-weight:600;cursor:pointer;transition:background .2s}
.btn:hover{background:#a29bfe}
.btn:disabled{opacity:.5;cursor:not-allowed}
.err{color:#ff6b6b;font-size:12px;text-align:center;margin-top:12px;min-height:18px}
</style>
</head>
<body>
<div class="card">
<h2>🔒 访问认证</h2>
<form id="f" onsubmit="return doLogin(event)">
<div class="field"><input id="pw" type="password" placeholder="请输入访问密码" autocomplete="current-password" autofocus></div>
<button class="btn" type="submit" id="btn">登 录</button>
</form>
<div class="err" id="err"></div>
</div>
<script>
async function doLogin(e){
  e.preventDefault();
  const btn=document.getElementById('btn'),err=document.getElementById('err'),pw=document.getElementById('pw');
  btn.disabled=true;err.textContent='';
  try{
    const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw.value})});
    if(r.ok){location.href='/';}
    else{const d=await r.json();err.textContent=d.error||'密码错误';}
  }catch(ex){err.textContent='网络错误';}
  btn.disabled=false;
}
</script>
</body>
</html>"""


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    password = _get_password()
    # 未设置密码 → 不启用认证
    if not password:
        return await call_next(request)

    path = request.url.path
    # 白名单路由放行
    if path in _AUTH_WHITELIST:
        return await call_next(request)

    # 校验 Cookie
    cookie_value = request.cookies.get("_s", "")
    if _verify_cookie(cookie_value):
        return await call_next(request)

    # 未认证
    if path.startswith("/api/"):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    # 页面请求返回登录页
    return Response(content=_LOGIN_PAGE, media_type="text/html")


@app.post("/api/auth/login")
async def auth_login(request: Request):
    """密码登录，成功后设置 Cookie。"""
    password = _get_password()
    if not password:
        return {"ok": True, "message": "认证未启用"}

    body = await request.json()
    submitted = (body.get("password") or "").strip()

    if not submitted or not hmac.compare_digest(submitted, password):
        return JSONResponse({"ok": False, "error": "密码错误"}, status_code=401)

    token = secrets.token_hex(32)
    _sessions.add(token)
    cookie_value = _make_cookie_value(token)

    resp = JSONResponse({"ok": True, "message": "登录成功"})
    resp.set_cookie(
        key="_s",
        value=cookie_value,
        httponly=True,
        samesite="lax",
        max_age=86400 * 7,  # 7 天
        path="/",
    )
    return resp


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    """登出，清除 Cookie 和 session。"""
    cookie_value = request.cookies.get("_s", "")
    if cookie_value and "." in cookie_value:
        token = cookie_value.rsplit(".", 1)[0]
        _sessions.discard(token)

    resp = JSONResponse({"ok": True, "message": "已登出"})
    resp.delete_cookie(key="_s", path="/")
    return resp


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

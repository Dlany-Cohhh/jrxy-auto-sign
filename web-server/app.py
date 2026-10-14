#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
今日校园自动签到 · Web 服务
============================
将 jrxyzuixingnx 签到引擎封装为可部署在 Windows / Linux 服务器上的 Web 服务，
提供管理界面（状态概览 / 手动签到 / 坐标与定时设置 / 运行日志）。

启动方式（二选一，见 README-部署手册.md）：
  Windows: web-server\\.venv\\Scripts\\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
  Linux  : web-server/.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000
  Docker : docker compose up -d --build

环境变量：
  HOST       监听地址，默认 127.0.0.1（服务器对外部署可设 0.0.0.0）
  PORT       监听端口，默认 8000
  WEB_TOKEN  可选；设置后所有接口需携带请求头 X-Web-Token
  SIGN_TIME  内置定时签到时间（24 小时制 HH:MM），默认 22:05
  AUTO_SIGN  内置定时开关，默认 1=启用 0=停用

安全提示：
  - 默认仅监听本机；对外部署务必设置 WEB_TOKEN 并启用 HTTPS/反向代理。
  - 学号/密码可在 Web 页面保存，写入 data/credentials.json（不入 git、绝不返回密码）；
    运行签到/探测时以环境变量传入引擎（优先于 config.yml）。config.yml 作为未在
    Web 保存时的兜底（豆包定时任务直连引擎时仍用 config.yml）。
  - 坐标/地址修改优先写入 data/override.json（命令行参数覆盖），不改动 config.yml 注释结构。
"""
import json
import logging
import os
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse

# ---------------- 路径 ----------------
ROOT = Path(__file__).resolve().parent.parent          # 项目根目录
ENGINE_DIR = ROOT / "jrxyzuixingnx"                    # 签到引擎目录
WEB_DIR = Path(__file__).resolve().parent
DATA_DIR = WEB_DIR / "data"
STATIC_DIR = WEB_DIR / "static"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "signlog.db"
OVERRIDE_PATH = DATA_DIR / "override.json"
CREDS_PATH = DATA_DIR / "credentials.json"
INDEX_HTML = STATIC_DIR / "index.html"

IS_WIN = os.name == "nt"
ENGINE_PY = ENGINE_DIR / (".venv/Scripts/python.exe" if IS_WIN else ".venv/bin/python")
SUBMIT_SCRIPT = ENGINE_DIR / "scripts" / "merged_ios_v4_submit.py"
RANGE_SCRIPT = ENGINE_DIR / "scripts" / "fetch_sign_range.py"

# ---------------- 运行配置（环境变量） ----------------
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
WEB_TOKEN = os.getenv("WEB_TOKEN", "").strip()
DEFAULT_SIGN_TIME = os.getenv("SIGN_TIME", "22:05")
DEFAULT_AUTO_SIGN = os.getenv("AUTO_SIGN", "1") == "1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("jrxy-web")

# ---------------- 数据库 ----------------
def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS sign_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                kind TEXT NOT NULL,
                result TEXT NOT NULL,
                detail TEXT NOT NULL
            )"""
        )

def save_log(kind: str, result: str, detail: str):
    try:
        with sqlite3.connect(DB_PATH) as c:
            c.execute(
                "INSERT INTO sign_log(ts, kind, result, detail) VALUES(?,?,?,?)",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), kind, result, detail[-4000:]),
            )
    except Exception as e:  # 日志失败不影响主流程
        log.warning("save_log failed: %s", e)

def fetch_logs(limit: int = 50):
    try:
        with sqlite3.connect(DB_PATH) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT ts, kind, result, detail FROM sign_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []

# ---------------- 配置读写 ----------------
def load_engine_config():
    """读取引擎 config.yml（只读）。"""
    with open(ENGINE_DIR / "config.yml", encoding="utf-8") as f:
        return yaml.safe_load(f)

def user_config():
    cfg = load_engine_config()
    return cfg["users"][0]["user"]

def load_override() -> dict:
    if OVERRIDE_PATH.exists():
        try:
            return json.loads(OVERRIDE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_override(d: dict):
    OVERRIDE_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

# ---------------- Web 保存的账号密码（data/credentials.json，不入 git） ----------------
def load_creds() -> dict:
    if CREDS_PATH.exists():
        try:
            return json.loads(CREDS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_creds(username: str, password: str):
    """username/password 同时为空 = 清除 Web 保存的账号。"""
    d = {}
    if username or password:
        d = {"username": username.strip(), "password": password}
    CREDS_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

def creds_env() -> dict:
    """引擎运行时注入的账号环境变量；账号密码必须同时存在才生效。"""
    cr = load_creds()
    if cr.get("username") and cr.get("password"):
        return {"JRXY_USERNAME": cr["username"], "JRXY_PASSWORD": cr["password"]}
    return {}

def read_device_id() -> str:
    for name in (".device_id", "device_id.txt"):
        p = ENGINE_DIR / name
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    return ""

def effective_info():
    """有效配置 = Web 保存的账号优先，否则 config.yml；坐标 = override 优先。"""
    ov = load_override()
    u = user_config()
    cr = load_creds()
    return {
        "school": u.get("schoolName", ""),
        "username": cr.get("username") or u.get("username", ""),
        "type": u.get("type", 2),
        "lon": ov.get("lon", u.get("lon")),
        "lat": ov.get("lat", u.get("lat")),
        "address": ov.get("address", u.get("address", "")),
    }

def mask_username(name: str) -> str:
    """完全掩码：不泄露学号任何位（含前缀）。"""
    return "***" if name else ""

# ---------------- 引擎执行 ----------------
def run_engine(dry: bool) -> str:
    if not ENGINE_PY.exists():
        return "引擎虚拟环境不存在: %s\n请先执行 deploy/install_windows.bat 或 deploy/install_linux.sh" % ENGINE_PY
    e = effective_info()
    args = []
    if not dry:
        args.append("--submit")
    args += ["--variant", "fkey_cat", "--protocol", "ios"]
    dev = read_device_id()
    if dev:
        args += ["--device-id", dev]
    if e["lon"] is not None:
        args += ["--lon", str(e["lon"])]
    if e["lat"] is not None:
        args += ["--lat", str(e["lat"])]
    if e["address"]:
        args += ["--address", e["address"]]
    cmd = [str(ENGINE_PY), "-W", "ignore", str(SUBMIT_SCRIPT)] + args
    env = dict(os.environ)
    env.update(creds_env())   # Web 保存的账号密码（有则优先，无则回退 config.yml）
    log.info("engine cmd: %s", " ".join(str(x) for x in cmd))
    try:
        r = subprocess.run(
            cmd, cwd=str(ENGINE_DIR), capture_output=True, text=True,
            timeout=180, encoding="utf-8", errors="replace", env=env,
        )
        out = (r.stdout or "").strip()
        if r.stderr and r.stderr.strip():
            out += "\n[stderr]\n" + r.stderr.strip()
        return out or "(无输出)"
    except subprocess.TimeoutExpired:
        return "执行超时（180 秒），引擎可能卡在登录/网络请求上。"
    except Exception as e:
        return "执行异常: %s" % e

def classify(out: str) -> str:
    s = out
    if any(k in s for k in ("SUCCESS", "签到成功")):
        return "签到成功"
    if "查寝未开始" in s:
        return "查寝未开始"
    if "签到失败" in s:
        return "签到失败"
    if "已签到" in s or "重复提交" in s:
        return "已签到（重复提交）"
    if "登录失败" in s or "Traceback" in s or "Exception" in s:
        return "执行异常"
    return "执行完成（见详情）"

# ---------------- 内置定时（线程版，零第三方依赖） ----------------
scheduler_state = {"enabled": DEFAULT_AUTO_SIGN, "time": DEFAULT_SIGN_TIME, "next": None}
_sched_wake = threading.Event()   # 设置变更时唤醒调度线程，立即重算下次执行

def _parse_hm(s: str):
    try:
        h, m = s.strip().split(":")
        h, m = int(h), int(m)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        return h, m
    except Exception:
        raise ValueError("时间格式应为 HH:MM（如 22:05）")

def _calc_next(time_str):
    """计算下次触发时间；格式非法时返回 (None, None)。"""
    try:
        h, m = _parse_hm(time_str)
    except ValueError:
        return None, None
    now = datetime.now()
    nxt = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return nxt, nxt.strftime("%Y-%m-%d %H:%M:%S")

def scheduler_loop():
    while True:
        st = scheduler_state
        if st["enabled"]:
            now = datetime.now()
            nxt, nxt_str = _calc_next(st["time"])
            if nxt is None:
                st["next"] = None
                _sched_wake.wait(timeout=60)
                _sched_wake.clear()
                continue
            st["next"] = nxt_str
            wait = (nxt - now).total_seconds()
            if wait > 60:
                # 距触发点超过 60 秒：分段等待，随时响应时间/开关变更
                _sched_wake.wait(timeout=60)
                _sched_wake.clear()
                continue
            # 距触发点 <= 60 秒：睡到点触发（期间被唤醒则重算）
            _sched_wake.wait(timeout=max(wait, 1))
            _sched_wake.clear()
            log.info("定时签到触发（%s）", st["time"])
            out = run_engine(dry=False)
            save_log("定时签到", classify(out), out)
        else:
            st["next"] = None
            _sched_wake.wait(timeout=30)
            _sched_wake.clear()

def _start_scheduler():
    # 启动时把 override 中的定时配置合并进来（override 优先于环境变量）
    ov = load_override()
    if "auto" in ov:
        scheduler_state["enabled"] = bool(ov["auto"])
    if "sign_time" in ov:
        try:
            _parse_hm(ov["sign_time"])
            scheduler_state["time"] = ov["sign_time"]
        except ValueError:
            pass
    threading.Thread(target=scheduler_loop, daemon=True, name="jrxy-scheduler").start()

# ---------------- FastAPI ----------------
def require_token(x_web_token: Optional[str] = Header(default=None)):
    if WEB_TOKEN and x_web_token != WEB_TOKEN:
        raise HTTPException(status_code=401, detail="未授权：缺少或错误的 X-Web-Token")

app = FastAPI(title="今日校园自动签到", docs_url="/api/docs", redoc_url=None)

@app.get("/", response_class=FileResponse)
def index(_t: None = Depends(require_token)):
    return FileResponse(INDEX_HTML)

@app.get("/api/status")
def api_status(_t: None = Depends(require_token)):
    e = effective_info()
    ov = load_override()
    return {
        "platform": "Windows" if IS_WIN else "Linux",
        "engine_ready": ENGINE_PY.exists(),
        "school": e["school"],
        "username": mask_username(e["username"]),
        "type": e["type"],
        "lon": e["lon"],
        "lat": e["lat"],
        "address": e["address"],
        "device_id": read_device_id() or "（未设置）",
        "use_override": "lon" in ov or "lat" in ov or "address" in ov,
        "scheduler": {
            "enabled": scheduler_state["enabled"],
            "time": scheduler_state["time"],
            "next": scheduler_state["next"],
        },
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

@app.get("/api/credentials")
def api_get_credentials(_t: None = Depends(require_token)):
    cr = load_creds()
    u = user_config()
    has_web = bool(cr.get("username") and cr.get("password"))
    return {
        "source": "web" if has_web else "config",
        "username": mask_username(cr.get("username") or u.get("username", "")),
        "username_set": bool(cr.get("username")),
        "password_set": bool(cr.get("password")),
        "note": "账号保存在本机 data/credentials.json，密码绝不回传；未在 Web 保存时使用 config.yml 兜底",
    }

@app.post("/api/credentials")
def api_set_credentials(body: dict, _t: None = Depends(require_token)):
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    if username and not password:
        raise HTTPException(status_code=400, detail="已填写学号，密码不能为空")
    if password and not username:
        raise HTTPException(status_code=400, detail="已填写密码，学号不能为空")
    save_creds(username, password)
    save_log("账号设置", "账号已通过 Web 保存" if username else "账号已清除",
             "username=%s" % (mask_username(username) or "(空)"))
    return {"ok": True, "source": "web" if (username and password) else "config",
            "username": mask_username(username)}

@app.post("/api/config")
def api_config(body: dict, _t: None = Depends(require_token)):
    lon, lat = body.get("lon"), body.get("lat")
    address = str(body.get("address", "")).strip()
    try:
        lon = float(lon) if lon is not None and str(lon).strip() != "" else None
        lat = float(lat) if lat is not None and str(lat).strip() != "" else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="经度/纬度必须是数字（保留 6 位小数）")
    if lon is not None and not (-180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="经度范围 -180 ~ 180")
    if lat is not None and not (-90 <= lat <= 90):
        raise HTTPException(status_code=400, detail="纬度范围 -90 ~ 90")
    ov = load_override()
    ov["lon"], ov["lat"], ov["address"] = lon, lat, address
    save_override(ov)
    save_log("配置更新", "坐标/地址已更新",
             "lon=%s lat=%s address=%s" % (lon, lat, address))
    return {"ok": True, "effective": effective_info()}

@app.post("/api/sign-range")
def api_sign_range(_t: None = Depends(require_token)):
    """从服务端任务详情拉取签到范围（signPlaceSelected：中心点+半径），
    并直接写入 data/override.json 作为签到地址立即生效。"""
    if not ENGINE_PY.exists():
        raise HTTPException(status_code=500, detail="引擎虚拟环境不存在: %s" % ENGINE_PY)
    if not RANGE_SCRIPT.exists():
        raise HTTPException(status_code=500, detail="引擎脚本不存在: %s" % RANGE_SCRIPT)
    cmd = [str(ENGINE_PY), "-W", "ignore", str(RANGE_SCRIPT)]
    env = dict(os.environ)
    env.update(creds_env())   # Web 保存的账号密码优先，无则回退 config.yml
    try:
        r = subprocess.run(cmd, cwd=str(ENGINE_DIR), capture_output=True, text=True,
                           timeout=120, encoding="utf-8", errors="replace", env=env)
    except subprocess.TimeoutExpired:
        save_log("范围获取", "获取失败", "执行超时（120 秒）")
        raise HTTPException(status_code=504, detail="获取签到范围超时（120 秒）")
    except Exception as e:
        save_log("范围获取", "获取失败", str(e)[:200])
        raise HTTPException(status_code=500, detail="获取签到范围异常: %s" % e)
    payload = None
    for line in (r.stdout or "").splitlines():
        if line.startswith("###RANGE###"):
            try:
                payload = json.loads(line[len("###RANGE###"):].strip())
            except Exception:
                payload = None
            break
    if not payload or not payload.get("ok"):
        err = (payload or {}).get("error") or ((r.stderr or r.stdout or "").strip()[-300:])
        save_log("范围获取", "获取失败", str(err)[:200])
        raise HTTPException(status_code=502, detail="获取失败: %s" % err)
    lon, lat = float(payload["lon"]), float(payload["lat"])
    ov = load_override()
    ov["lon"], ov["lat"], ov["address"] = lon, lat, payload.get("address") or ov.get("address", "")
    save_override(ov)
    detail = "center=(%.8f, %.8f) radius=%sm %s 窗口 %s~%s" % (
        lon, lat, payload.get("radius"), payload.get("taskName", ""),
        payload.get("window_begin", ""), payload.get("window_end", ""))
    save_log("范围获取", "签到范围已获取并应用", detail)
    return {"ok": True, "range": payload,
            "note": "已写入 data/override.json：坐标与地址更新为服务端签到范围中心点（半径 %sm）" % payload.get("radius")}

@app.post("/api/schedule")
def api_schedule(body: dict, _t: None = Depends(require_token)):
    enabled = bool(body.get("enabled", scheduler_state["enabled"]))
    t = str(body.get("time", scheduler_state["time"])).strip()
    try:
        _parse_hm(t)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    scheduler_state["enabled"] = enabled
    scheduler_state["time"] = t
    _, nxt_str = _calc_next(t)          # 立即重算下次执行时间并返回
    scheduler_state["next"] = nxt_str
    ov = load_override()
    ov["auto"] = enabled
    ov["sign_time"] = t
    save_override(ov)
    save_log("定时更新", "定时签到设置已更新",
             "enabled=%s time=%s" % (enabled, t))
    _sched_wake.set()   # 唤醒调度线程，按新配置重新等待
    return {"ok": True, "scheduler": {
        "enabled": enabled, "time": t, "next": nxt_str}}

@app.post("/api/run")
def api_run(body: dict, _t: None = Depends(require_token)):
    mode = str(body.get("mode", "dry"))
    if mode not in ("dry", "submit"):
        raise HTTPException(status_code=400, detail="mode 仅支持 dry / submit")
    kind = "手动探测" if mode == "dry" else "手动签到"
    out = run_engine(dry=(mode == "dry"))
    result = classify(out)
    save_log(kind, result, out)
    return {"ok": True, "result": result, "detail": out[-4000:]}

@app.get("/api/logs")
def api_logs(limit: int = 50, _t: None = Depends(require_token)):
    limit = max(1, min(limit, 200))
    return {"logs": fetch_logs(limit)}

# ---------------- 启动 ----------------
init_db()
_start_scheduler()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)

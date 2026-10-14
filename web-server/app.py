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
  - 明文密码仅存在于 jrxyzuixingnx/config.yml，界面与接口绝不返回密码。
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
INDEX_HTML = STATIC_DIR / "index.html"

IS_WIN = os.name == "nt"
ENGINE_PY = ENGINE_DIR / (".venv/Scripts/python.exe" if IS_WIN else ".venv/bin/python")
SUBMIT_SCRIPT = ENGINE_DIR / "scripts" / "merged_ios_v4_submit.py"

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

def read_device_id() -> str:
    for name in (".device_id", "device_id.txt"):
        p = ENGINE_DIR / name
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    return ""

def effective_info():
    """有效配置 = override 优先，否则用 config.yml。"""
    ov = load_override()
    u = user_config()
    return {
        "school": u.get("schoolName", ""),
        "username": u.get("username", ""),
        "type": u.get("type", 2),
        "lon": ov.get("lon", u.get("lon")),
        "lat": ov.get("lat", u.get("lat")),
        "address": ov.get("address", u.get("address", "")),
    }

def mask_username(name: str) -> str:
    if not name:
        return ""
    if len(name) <= 4:
        return name[0] + "***"
    return name[:4] + "***"

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
    log.info("engine cmd: %s", " ".join(str(x) for x in cmd))
    try:
        r = subprocess.run(
            cmd, cwd=str(ENGINE_DIR), capture_output=True, text=True,
            timeout=180, encoding="utf-8", errors="replace",
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

def _parse_hm(s: str):
    try:
        h, m = s.strip().split(":")
        h, m = int(h), int(m)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        return h, m
    except Exception:
        raise ValueError("时间格式应为 HH:MM（如 22:05）")

def scheduler_loop():
    while True:
        st = scheduler_state
        if st["enabled"]:
            now = datetime.now()
            try:
                h, m = _parse_hm(st["time"])
            except ValueError:
                st["next"] = None
                time.sleep(60)
                continue
            nxt = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            st["next"] = nxt.strftime("%Y-%m-%d %H:%M:%S")
            wait = max((nxt - now).total_seconds(), 1)
            time.sleep(wait)
            log.info("定时签到触发（%s）", st["time"])
            out = run_engine(dry=False)
            save_log("定时签到", classify(out), out)
        else:
            st["next"] = None
            time.sleep(30)

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
    ov = load_override()
    ov["auto"] = enabled
    ov["sign_time"] = t
    save_override(ov)
    save_log("定时更新", "定时签到设置已更新",
             "enabled=%s time=%s" % (enabled, t))
    return {"ok": True, "scheduler": {
        "enabled": enabled, "time": t, "next": scheduler_state["next"]}}

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

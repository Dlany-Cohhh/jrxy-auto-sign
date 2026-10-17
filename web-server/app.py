#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
今日校园自动签到 · Web 服务（多账号版）
=====================================
将 jrxy-engine 签到引擎封装为多账号 Web 管理服务。

账号数据源（与 scripts/batch_submit.py 共用，见 accounts/README.md）：
  accounts/<name>.yml         每账号配置（users 列表只含该账号）
  accounts/<name>.device_id   每账号固定 deviceId（首次运行自动生成；迁移时手工写入原 ID）

启动方式：
  Windows: web-server\\.venv\\Scripts\\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
  Linux  : web-server/.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000

环境变量：
  HOST                监听地址，默认 127.0.0.1（对外部署可设 0.0.0.0）
  PORT                监听端口，默认 8000
  WEB_TOKEN           可选；设置后所有接口需携带请求头 X-Web-Token
  SIGN_TIME           内置定时签到时间（24 小时制 HH:MM），默认 22:05
  AUTO_SIGN           内置定时开关，默认 1=启用 0=停用
  JRXY_ACCOUNTS_DIR   账号配置目录，默认 <项目根>/accounts

安全提示：
  - 默认仅监听本机；对外部署务必设置 WEB_TOKEN 并启用 HTTPS/反向代理。
  - accounts/*.yml 与 *.device_id 已被 .gitignore 排除，密码绝不回传。
  - 每账号 deviceId 独立固定；已有绑定账号迁移时请在页面填写原 deviceId，
    切勿让其重新生成，否则触发“更换手机频繁”风控。
"""
import base64
import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
import uuid
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse

# ---------------- 路径 ----------------
ROOT = Path(__file__).resolve().parent.parent          # 项目根目录
ENGINE_DIR = ROOT / "jrxy-engine"                    # 签到引擎目录
WEB_DIR = Path(__file__).resolve().parent
DATA_DIR = WEB_DIR / "data"
STATIC_DIR = WEB_DIR / "static"
ACCOUNTS_DIR = Path(os.getenv("JRXY_ACCOUNTS_DIR", str(ROOT / "accounts")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "signlog.db"
SCHED_PATH = DATA_DIR / "schedule.json"
INDEX_HTML = STATIC_DIR / "index.html"

IS_WIN = os.name == "nt"
ENGINE_PY = ENGINE_DIR / (".venv/Scripts/python.exe" if IS_WIN else ".venv/bin/python")
SUBMIT_SCRIPT = ENGINE_DIR / "scripts" / "merged_ios_v4_submit.py"
RANGE_SCRIPT = ENGINE_DIR / "scripts" / "fetch_sign_range.py"
TESTLOGIN_SCRIPT = ENGINE_DIR / "scripts" / "test_login.py"
BATCH_SCRIPT = ENGINE_DIR / "scripts" / "batch_submit.py"

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
    except Exception as e:
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

def clear_logs():
    with sqlite3.connect(DB_PATH) as c:
        c.execute("DELETE FROM sign_log")

# ---------------- 账号（accounts/ 目录，与 batch_submit.py 共用） ----------------
NAME_RE = re.compile(r"^[A-Za-z0-9_\-\.\u4e00-\u9fa5]{1,32}$")

def valid_account_name(name: str) -> bool:
    return bool(name) and not name.startswith("_") and NAME_RE.match(name) \
        and name not in (".", "..")

def account_paths(name: str):
    return ACCOUNTS_DIR / (name + ".yml"), ACCOUNTS_DIR / (name + ".device_id")

def list_accounts() -> list:
    if not ACCOUNTS_DIR.is_dir():
        return []
    out = []
    for f in sorted(os.listdir(ACCOUNTS_DIR)):
        if f.endswith(".yml") and not f.startswith("_"):
            out.append(f[:-4])
    return out

def read_did(did_file: Path) -> str:
    """读取 deviceId（兼容 BOM）；文件不存在或为空返回 ''。"""
    if did_file.exists():
        try:
            return did_file.read_text(encoding="utf-8-sig").strip()
        except Exception:
            return ""
    return ""

def ensure_device_id(name: str) -> str:
    """与 batch_submit.py 同规则：已存在且非空则沿用，否则生成一次（小写 UUID）。"""
    _, did_file = account_paths(name)
    val = read_did(did_file)
    if val:
        return val
    if did_file.exists():
        raise HTTPException(status_code=400, detail=f"{name}.device_id 内容为空！"
                            "请填入该账号已绑定的 deviceId，或删除该文件让其自动生成（仅限无绑定新账号）")
    did = str(uuid.uuid4())
    did_file.write_text(did, encoding="utf-8")
    return did

def read_account(name: str):
    """返回 (user dict, device_id str)；不存在则 404。"""
    yml, _ = account_paths(name)
    if not valid_account_name(name) or not yml.exists():
        raise HTTPException(status_code=404, detail=f"账号 {name} 不存在")
    try:
        cfg = yaml.safe_load(yml.read_text(encoding="utf-8"))
        user = cfg["users"][0]["user"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"账号 {name} 配置解析失败: {e}")
    _, did_file = account_paths(name)
    return user, read_did(did_file)

def dump_account(user: dict, http_proxy: str = "") -> str:
    # 引擎读取的是 user["httpProxy"]（user 级）；顶层 httpProxy 仅为兼容模板样式保留
    user = dict(user)
    user["httpProxy"] = http_proxy or ""
    cfg = {
        "notifyOption": {"method": 0, "mailApiUrl": ""},
        "ocrOption": {"SecretId": "", "SecretKey": ""},
        "httpProxy": http_proxy or "",
        "users": [{"user": user}],
    }
    return yaml.dump(cfg, allow_unicode=True, sort_keys=False, default_flow_style=False)

def write_account(name: str, user: dict, http_proxy: str = ""):
    yml, _ = account_paths(name)
    yml.write_text(dump_account(user, http_proxy), encoding="utf-8")

def mask_username(name: str) -> str:
    if not name:
        return ""
    if len(name) <= 4:
        return name[0] + "***"
    return name[:4] + "***"

def normalize_optional_num(v, default=None):
    """数字字段：空串/None → default；否则 float。"""
    if v is None or str(v).strip() == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="经度/纬度必须是数字")

def account_summary(name: str) -> dict:
    user, dev = read_account(name)
    return {
        "name": name,
        "school": user.get("schoolName", ""),
        "username": user.get("username", ""),
        "type": user.get("type", 2),
        "lon": user.get("lon"),
        "lat": user.get("lat"),
        "address": user.get("address", ""),
        "photo": user.get("photo", ""),
        "password_set": bool(user.get("password")),
        "device_id": dev or "（未设置，首次运行自动生成）",
    }

# ---------------- 引擎执行 ----------------
def run_cmd(cmd: list, timeout: int = 180, extra_env: Optional[dict] = None) -> str:
    env = dict(os.environ)
    env["JRXY_ACCOUNTS_DIR"] = str(ACCOUNTS_DIR)
    if extra_env:
        env.update(extra_env)
    try:
        r = subprocess.run(cmd, cwd=str(ENGINE_DIR), capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace", env=env)
    except subprocess.TimeoutExpired:
        return "执行超时（%ss），引擎可能卡在登录/网络请求上。" % timeout
    except Exception as e:
        return "执行异常: %s" % e
    out = (r.stdout or "").strip()
    if r.stderr and r.stderr.strip():
        out += "\n[stderr]\n" + r.stderr.strip()
    return out or "(无输出)"

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
    if "登录失败" in s or "Traceback" in s or "Exception" in s or "执行超时" in s:
        return "执行异常"
    return "执行完成（见详情）"

def notify_head(result: str, kind: str = "签到") -> str:
    """把结果归类为状态词：签到/探测 成功、失败、查寝未开始、无任务待签。"""
    r = (result or "").strip()
    if "查寝未开始" in r:
        return "查寝未开始"
    if "没有未签到的任务" in r:
        return "无任务待签"
    if any(k in r for k in ("成功", "完成", "重复提交", "已签到")):
        return kind + "成功"
    return kind + "失败"

def send_notify(name: str, title: str, msg: str):
    """按账号 notifyOption 推送签到结果。
    method=2 走 Server酱/任意 Webhook（rcvOption 填 SendKey 或完整地址）。
    失败静默不影响主流程。"""
    try:
        user, _ = read_account(name)
        no = user.get("notifyOption") or {}
        if int(no.get("method", 0) or 0) != 2:
            return
        rcv = str(no.get("rcvOption", "")).strip()
        if not rcv:
            return
        # 兼容：只填 SendKey（SCT开头）时自动拼成 Server酱推送地址
        if rcv.startswith("SCT"):
            rcv = "https://sctapi.ftqq.com/%s.send" % rcv
        data = urllib.parse.urlencode({"title": title, "desp": msg}).encode("utf-8")
        req = urllib.request.Request(
            rcv, data=data, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        log.info("notify skipped for %s: %s", name, e)

def notify_batch(out: str, kind: str = "签到"):
    """解析批量脚本输出，对每个账号逐条推送其结果。"""
    names = list_accounts()
    blocks = re.split(r"账号: (\S+)", out)
    for i in range(1, len(blocks), 2):
        nm = blocks[i].strip(" :")
        if nm not in names:
            continue
        body = blocks[i + 1] or ""
        result = classify(body)
        head = notify_head(result, kind="探测" if "探测" in kind else "签到")
        send_notify(nm, "【%s】%s" % (head, nm),
                    "%s\n时间：%s\n\n%s" % (
                        head, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        body.strip()[-1200:] or "(无输出)"))

def run_account(name: str, dry: bool) -> str:
    if not ENGINE_PY.exists():
        return "引擎虚拟环境不存在: %s\n请先执行 deploy/install_windows.bat 或 deploy/install_linux.sh" % ENGINE_PY
    yml, _ = account_paths(name)
    try:
        did = ensure_device_id(name)
    except HTTPException as e:
        return str(e.detail)
    cmd = [str(ENGINE_PY), "-W", "ignore", str(SUBMIT_SCRIPT),
           "--dry" if dry else "--submit",
           "--variant", "fkey_cat", "--protocol", "ios",
           "--config", str(yml), "--device-id", did]
    log.info("run_account %s dry=%s: %s", name, dry, " ".join(str(x) for x in cmd))
    return run_cmd(cmd)

def batch_run(dry: bool) -> str:
    if not ENGINE_PY.exists():
        return "引擎虚拟环境不存在: %s" % ENGINE_PY
    if not BATCH_SCRIPT.exists():
        return "批量脚本不存在: %s" % BATCH_SCRIPT
    cmd = [str(ENGINE_PY), "-W", "ignore", str(BATCH_SCRIPT),
           "--dry" if dry else "--submit"]
    log.info("batch_run dry=%s accounts_dir=%s", dry, ACCOUNTS_DIR)
    return run_cmd(cmd, timeout=600)

def parse_marker(out: str, marker: str):
    for line in out.splitlines():
        if line.startswith(marker):
            try:
                return json.loads(line[len(marker):].strip())
            except Exception:
                return None
    return None

# ---------------- 定时（线程版，触发时批量跑全部账号） ----------------
def load_schedule():
    st = {"enabled": DEFAULT_AUTO_SIGN, "time": DEFAULT_SIGN_TIME, "next": None}
    if SCHED_PATH.exists():
        try:
            d = json.loads(SCHED_PATH.read_text(encoding="utf-8"))
            st["enabled"] = bool(d.get("enabled", st["enabled"]))
            st["time"] = str(d.get("time", st["time"]))
        except Exception:
            pass
    return st

def save_schedule(enabled: bool, time_str: str):
    SCHED_PATH.write_text(json.dumps({"enabled": enabled, "time": time_str},
                                     ensure_ascii=False, indent=2), encoding="utf-8")

scheduler_state = load_schedule()
_sched_wake = threading.Event()

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
                _sched_wake.wait(timeout=60)
                _sched_wake.clear()
                continue
            _sched_wake.wait(timeout=max(wait, 1))
            _sched_wake.clear()
            log.info("定时批量签到触发（%s）", st["time"])
            out = batch_run(dry=False)
            save_log("定时批量签到", classify(out), out)
            notify_batch(out, "定时签到")
        else:
            st["next"] = None
            _sched_wake.wait(timeout=30)
            _sched_wake.clear()

def _start_scheduler():
    threading.Thread(target=scheduler_loop, daemon=True, name="jrxy-scheduler").start()

# ---------------- FastAPI ----------------
def require_token(x_web_token: Optional[str] = Header(default=None)):
    if WEB_TOKEN and x_web_token != WEB_TOKEN:
        raise HTTPException(status_code=401, detail="未授权：缺少或错误的 X-Web-Token")

app = FastAPI(title="今日校园自动签到 · 多账号管理台", docs_url="/api/docs", redoc_url=None)

@app.get("/", response_class=FileResponse)
def index():
    return FileResponse(INDEX_HTML)

@app.get("/school_list.js")
def school_list_js():
    return FileResponse(STATIC_DIR / "school_list.js", media_type="application/javascript")

@app.get("/api/status")
def api_status(_t: None = Depends(require_token)):
    return {
        "platform": "Windows" if IS_WIN else "Linux",
        "engine_ready": ENGINE_PY.exists(),
        "accounts_dir": str(ACCOUNTS_DIR),
        "account_count": len(list_accounts()),
        "scheduler": {
            "enabled": scheduler_state["enabled"],
            "time": scheduler_state["time"],
            "next": scheduler_state["next"],
        },
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

# ---------------- 账号管理 ----------------
@app.get("/api/accounts")
def api_accounts(_t: None = Depends(require_token)):
    names = list_accounts()
    return {"accounts": [account_summary(n) for n in names]}

@app.get("/api/accounts/{name}")
def api_account_get(name: str, _t: None = Depends(require_token)):
    user, dev = read_account(name)
    no = user.get("notifyOption") or {}
    return {
        "name": name,
        "schoolName": user.get("schoolName", ""),
        "type": user.get("type", 2),
        "httpProxy": user.get("httpProxy", ""),
        "lon": user.get("lon"),
        "lat": user.get("lat"),
        "address": user.get("address", ""),
        "abnormalReason": user.get("abnormalReason", ""),
        "photo": user.get("photo", ""),
        "checkTitle": user.get("checkTitle", 0),
        "username": user.get("username", ""),
        "username_set": bool(user.get("username")),
        "password_set": bool(user.get("password")),
        "device_id": dev,
        "notify_method": int(no.get("method", 0) or 0),
        "notify_rcv": str(no.get("rcvOption", "")).strip(),
    }

def build_user_from_body(body: dict, existing: dict = None) -> dict:
    """构造/合并 user 字典；existing 为空表示新建（必填 schoolName/username/password）。"""
    cur = dict(existing or {})
    schoolName = str(body.get("schoolName", "")).strip() or cur.get("schoolName", "")
    username = str(body.get("username", "")).strip() or cur.get("username", "")
    password = str(body.get("password", "")) or cur.get("password", "")
    if not existing and not (schoolName and username and password):
        raise HTTPException(status_code=400,
                            detail="新建账号必须填写：学校名称 / 学号 / 密码")
    try:
        typ = int(body.get("type", cur.get("type", 2)))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="任务类型必须是 0~4 的整数")
    if typ not in (0, 1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="任务类型仅支持 0收集/1签到/2查寝/3政工/4日志")
    lon = normalize_optional_num(body.get("lon"), cur.get("lon"))
    lat = normalize_optional_num(body.get("lat"), cur.get("lat"))
    if lon is not None and not (-180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="经度范围 -180 ~ 180")
    if lat is not None and not (-90 <= lat <= 90):
        raise HTTPException(status_code=400, detail="纬度范围 -90 ~ 90")
    try:
        checkTitle = int(body.get("checkTitle", cur.get("checkTitle", 0)) or 0)
    except (TypeError, ValueError):
        checkTitle = 0
    # 消息推送：method 0=禁用 2=Server酱/任意Webhook（rcvOption 填推送地址）
    cur_no = cur.get("notifyOption") or {}
    if not isinstance(cur_no, dict):
        cur_no = {}
    try:
        n_method = int(body.get("notifyMethod", cur_no.get("method", 0)) or 0)
    except (TypeError, ValueError):
        n_method = 0
    if n_method not in (0, 1, 2):
        n_method = 0
    notify = {
        "method": n_method,
        "rcvOption": str(body.get("notifyRcv", cur_no.get("rcvOption", ""))).strip(),
    }
    user = {
        "type": typ,
        "schoolName": schoolName,
        "username": username,
        "password": password,
        "address": str(body.get("address", cur.get("address", ""))).strip(),
        "lon": lon,
        "lat": lat,
        "abnormalReason": str(body.get("abnormalReason", cur.get("abnormalReason", ""))).strip(),
        "photo": str(body.get("photo", cur.get("photo", ""))).strip(),
        "checkTitle": checkTitle,
        "notifyOption": notify,
    }
    return user, str(body.get("httpProxy", "")).strip()

@app.post("/api/accounts")
def api_account_create(body: dict, _t: None = Depends(require_token)):
    name = str(body.get("name", "")).strip()
    if not valid_account_name(name):
        raise HTTPException(status_code=400,
                            detail="账号名需为 1~32 位中英文/数字/_- 且不能以 _ 开头")
    yml, did_file = account_paths(name)
    if yml.exists():
        raise HTTPException(status_code=400, detail=f"账号 {name} 已存在")
    user, proxy = build_user_from_body(body)
    write_account(name, user, proxy)
    dev = str(body.get("deviceId", "")).strip()
    if dev:
        did_file.write_text(dev, encoding="utf-8")
    save_log("账号管理", "账号已创建", "name=%s school=%s" % (name, user["schoolName"]))
    return {"ok": True, "account": account_summary(name),
            "note": ("已写入固定 deviceId" if dev else "deviceId 未设置，首次运行自动生成")}

@app.post("/api/accounts/{name}")
def api_account_update(name: str, body: dict, _t: None = Depends(require_token)):
    user, dev = read_account(name)
    new_user, proxy = build_user_from_body(body, existing=user)
    # 密码留空 = 保持原密码（build_user_from_body 已处理）；账号留空同理
    write_account(name, new_user, proxy)
    dev_new = str(body.get("deviceId", "")).strip()
    if dev_new:
        _, did_file = account_paths(name)
        did_file.write_text(dev_new, encoding="utf-8")
    save_log("账号管理", "账号已更新", "name=%s school=%s" % (name, new_user["schoolName"]))
    return {"ok": True, "account": account_summary(name)}

@app.delete("/api/accounts/{name}")
def api_account_delete(name: str, _t: None = Depends(require_token)):
    read_account(name)   # 校验存在
    yml, did_file = account_paths(name)
    yml.unlink(missing_ok=True)
    # deviceId 文件保留（若账号重新接入，可沿用原绑定；文件不随 yml 被批量脚本扫描）
    save_log("账号管理", "账号已删除", "name=%s（deviceId 文件保留以防误删绑定）" % name)
    return {"ok": True, "note": "已删除账号配置；deviceId 文件已保留（重新接入可沿用原绑定）"}

# ---------------- 账号操作：探测/签到/登录测试/签到范围/照片 ----------------
@app.post("/api/accounts/{name}/run")
def api_account_run(name: str, body: dict, _t: None = Depends(require_token)):
    mode = str(body.get("mode", "dry"))
    if mode not in ("dry", "submit"):
        raise HTTPException(status_code=400, detail="mode 仅支持 dry / submit")
    read_account(name)
    kind = f"账号[{name}] {'探测' if mode == 'dry' else '签到'}"
    out = run_account(name, dry=(mode == "dry"))
    result = classify(out)
    save_log(kind, result, out)
    # 探测与签到都推送（头部状态化：探测成功/探测失败/查寝未开始/无任务待签）
    head = notify_head(result, kind="探测" if mode == "dry" else "签到")
    send_notify(name, "【%s】%s" % (head, name),
                "%s\n时间：%s\n\n%s" % (
                    head, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    out.strip()[-1200:] or "(无输出)"))
    return {"ok": True, "result": result, "detail": out[-4000:]}

@app.post("/api/accounts/{name}/test-login")
def api_account_test_login(name: str, _t: None = Depends(require_token)):
    read_account(name)
    if not ENGINE_PY.exists():
        raise HTTPException(status_code=500, detail="引擎虚拟环境不存在")
    if not TESTLOGIN_SCRIPT.exists():
        raise HTTPException(status_code=500, detail="引擎脚本不存在")
    yml, _ = account_paths(name)
    cmd = [str(ENGINE_PY), "-W", "ignore", str(TESTLOGIN_SCRIPT), "--config", str(yml)]
    out = run_cmd(cmd, timeout=60)
    payload = parse_marker(out, "###LOGIN###")
    if not payload:
        save_log(f"账号[{name}] 登录测试", "测试失败", (out or "")[-300:])
        raise HTTPException(status_code=502, detail="无法解析结果: %s" % (out[-300:] or "无输出"))
    if payload.get("ok"):
        save_log(f"账号[{name}] 登录测试", "登录成功",
                 "host=%s cookies=%s" % (payload.get("host"), ",".join(payload.get("cookies") or [])))
    else:
        save_log(f"账号[{name}] 登录测试", "登录失败", str(payload.get("error"))[:200])
    return {"ok": bool(payload.get("ok")), "host": payload.get("host", ""),
            "cookies": payload.get("cookies", []), "error": payload.get("error")}

@app.post("/api/accounts/{name}/sign-range")
def api_account_sign_range(name: str, _t: None = Depends(require_token)):
    user, _ = read_account(name)
    if not ENGINE_PY.exists():
        raise HTTPException(status_code=500, detail="引擎虚拟环境不存在")
    if not RANGE_SCRIPT.exists():
        raise HTTPException(status_code=500, detail="引擎脚本不存在")
    yml, _ = account_paths(name)
    cmd = [str(ENGINE_PY), "-W", "ignore", str(RANGE_SCRIPT), "--config", str(yml)]
    out = run_cmd(cmd, timeout=120)
    payload = parse_marker(out, "###RANGE###")
    if not payload or not payload.get("ok"):
        err = (payload or {}).get("error") or (out[-300:] or "无输出")
        save_log(f"账号[{name}] 范围获取", "获取失败", str(err)[:200])
        raise HTTPException(status_code=502, detail="获取失败: %s" % err)
    lon, lat = float(payload["lon"]), float(payload["lat"])
    user["lon"], user["lat"] = lon, lat
    user["address"] = payload.get("address") or user.get("address", "")
    write_account(name, user)
    detail = "center=(%.8f, %.8f) radius=%sm %s 窗口 %s~%s" % (
        lon, lat, payload.get("radius"), payload.get("taskName", ""),
        payload.get("window_begin", ""), payload.get("window_end", ""))
    save_log(f"账号[{name}] 范围获取", "签到范围已获取并应用", detail)
    return {"ok": True, "range": payload, "account": account_summary(name)}

@app.post("/api/accounts/{name}/photo")
def api_account_photo(name: str, body: dict, _t: None = Depends(require_token)):
    user, _ = read_account(name)
    fname = os.path.basename(str(body.get("name", "")).strip())
    data = str(body.get("data", ""))
    if not fname or not data:
        raise HTTPException(status_code=400, detail="缺少文件名或图片数据")
    if not fname.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
        raise HTTPException(status_code=400, detail="仅支持 jpg/png/webp/gif 图片")
    try:
        raw = base64.b64decode(data.split(",")[-1])
    except Exception:
        raise HTTPException(status_code=400, detail="图片数据解码失败")
    if not raw:
        raise HTTPException(status_code=400, detail="图片内容为空")
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片不能超过 5MB")
    adir = ACCOUNTS_DIR / name
    adir.mkdir(parents=True, exist_ok=True)
    target = adir / fname
    target.write_bytes(raw)
    # 引擎按 jrxy-engine/ 根目录解析 photo；accounts 在项目根，故用 ../accounts/<name>/<file>
    user["photo"] = "../accounts/%s/%s" % (name, fname)
    write_account(name, user)
    save_log(f"账号[{name}] 照片", "签到照片已上传", "file=%s size=%dB" % (fname, len(raw)))
    return {"ok": True, "photo": user["photo"], "size": len(raw)}

# ---------------- 批量运行 / 定时 / 日志 ----------------
@app.post("/api/batch-run")
def api_batch_run(body: dict, _t: None = Depends(require_token)):
    mode = str(body.get("mode", "dry"))
    if mode not in ("dry", "submit"):
        raise HTTPException(status_code=400, detail="mode 仅支持 dry / submit")
    kind = "批量探测" if mode == "dry" else "批量签到"
    out = batch_run(dry=(mode == "dry"))
    result = classify(out)
    if "批量完成" in out:
        result = out.split("批量完成", 1)[1].strip().splitlines()[0].strip()
    save_log(kind, result, out)
    # 批量探测与批量签到都推送（逐账号，头部状态化）
    notify_batch(out, kind)
    return {"ok": True, "result": result, "detail": out[-4000:]}

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
    _, nxt_str = _calc_next(t)
    scheduler_state["next"] = nxt_str
    save_schedule(enabled, t)
    save_log("定时更新", "定时批量签到设置已更新", "enabled=%s time=%s" % (enabled, t))
    _sched_wake.set()
    return {"ok": True, "scheduler": {"enabled": enabled, "time": t, "next": nxt_str}}

@app.get("/api/logs")
def api_logs(limit: int = 50, _t: None = Depends(require_token)):
    limit = max(1, min(limit, 200))
    return {"logs": fetch_logs(limit)}

@app.post("/api/logs/clear")
def api_logs_clear(_t: None = Depends(require_token)):
    clear_logs()
    save_log("日志管理", "日志已清空", "由 Web 页面手动清除")
    return {"ok": True}

# ---------------- 启动 ----------------
init_db()
_start_scheduler()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)

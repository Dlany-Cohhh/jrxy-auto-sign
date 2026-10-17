# -*- coding: utf-8 -*-
"""多账号批量签到：遍历 accounts/*.yml，逐个账号执行引擎提交脚本。

用法（Windows / Linux 均可，脚本会自动探测 .venv 解释器）：
  # 只看账号清单（不执行、不联网、不生成 deviceId）
  python scripts/batch_submit.py --list

  # 只探测全部账号：登录 + 查任务 + 领密钥 + 构造提交体（无副作用）
  python scripts/batch_submit.py --dry

  # 真实提交全部账号（会消耗签到机会，注意风控）
  python scripts/batch_submit.py --submit

  # 只处理某一个账号（按 yml 文件名，不含 .yml）
  python scripts/batch_submit.py --submit --account alice

环境变量（可选）：
  JRXY_ACCOUNTS_DIR  账号配置目录，默认 <项目根>/accounts
  JRXY_INTERVAL      每账号间隔秒数，默认 10（错峰防风控）
  JRXY_PYTHON        引擎 python 可执行文件，默认自动探测 .venv

每账号两个文件（位于 accounts/，已被 .gitignore 排除，不会提交）：
  <name>.yml        账号配置（结构同 config.example.yml，users 列表只放该账号；
                    以 _ 开头的文件视为模板/禁用，不会被执行）
  <name>.device_id  该账号固定的 deviceId：
                      - 新账号（无任何绑定）：首次运行自动生成小写 UUID，之后永久复用；
                      - 已有绑定账号（真机/旧服务器签过到）：把原 deviceId 原样写进
                        此文件，切勿让脚本重新生成，否则触发“更换手机频繁”风控。
"""
import argparse
import os
import subprocess
import sys
import time
import uuid

import yaml

# ---------------- 路径 ----------------
ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # jrxy-engine/
PROJECT_ROOT = os.path.dirname(ENGINE_DIR)                                  # 项目根
ACCOUNTS_DIR = os.getenv("JRXY_ACCOUNTS_DIR",
                         os.path.join(PROJECT_ROOT, "accounts"))
SUBMIT_SCRIPT = os.path.join(ENGINE_DIR, "scripts", "merged_ios_v4_submit.py")
INTERVAL = int(os.getenv("JRXY_INTERVAL", "10"))


def engine_python() -> str:
    """优先环境变量 JRXY_PYTHON，否则按平台探测 .venv 里的解释器。"""
    p = os.getenv("JRXY_PYTHON")
    if p:
        return p
    if os.name == "nt":
        return os.path.join(ENGINE_DIR, ".venv", "Scripts", "python.exe")
    return os.path.join(ENGINE_DIR, ".venv", "bin", "python")


def account_files():
    """返回 [(文件名不含后缀, yml 绝对路径, device_id 绝对路径)]，按名排序。
    跳过以 _ 开头的文件（模板/禁用，如 _example.yml）。"""
    if not os.path.isdir(ACCOUNTS_DIR):
        return []
    out = []
    for f in sorted(os.listdir(ACCOUNTS_DIR)):
        if not f.endswith(".yml"):
            continue
        stem = f[:-4]
        if stem.startswith("_"):
            continue
        out.append((stem,
                    os.path.join(ACCOUNTS_DIR, f),
                    os.path.join(ACCOUNTS_DIR, stem + ".device_id")))
    return out


def ensure_device_id(did_file: str) -> str:
    """每个账号独立固定 deviceId：文件已存在且非空则沿用（绑定不变），否则生成一次。"""
    if os.path.exists(did_file):
        # utf-8-sig：兼容 Windows 编辑器/命令写入时可能带有的 BOM
        val = open(did_file, encoding="utf-8-sig").read().strip()
        if val:
            return val
        raise ValueError(
            f"{did_file} 内容为空！请填入该账号已绑定的 deviceId，"
            "或删除该文件让脚本重新生成（仅限无绑定新账号）")
    did = str(uuid.uuid4())   # 小写带连字符，与 App 端 UUID.randomUUID() 一致
    with open(did_file, "w", encoding="utf-8") as f:
        f.write(did)
    print(f"  [新账号] 已生成并固定 deviceId -> {did_file}")
    return did


def run_one(stem: str, yml_path: str, did_file: str, submit: bool) -> str:
    """对单个账号执行引擎；返回归类结果 ok / warn / fail。"""
    try:
        cfg = yaml.safe_load(open(yml_path, encoding="utf-8"))
        name = cfg["users"][0]["user"].get("username", stem)
    except Exception as e:
        print(f"  ❌ {stem}: 配置读取失败 ({e})")
        return "fail"
    try:
        did = ensure_device_id(did_file)
    except ValueError as e:
        print(f"  ❌ {stem}: {e}")
        return "fail"
    print("=" * 60)
    print(f"账号: {name}  (deviceId: {did[:8]}…, 文件: {os.path.basename(did_file)})")
    cmd = [engine_python(), "-W", "ignore", SUBMIT_SCRIPT,
           "--submit" if submit else "--dry",
           "--variant", "fkey_cat", "--protocol", "ios",
           "--config", yml_path, "--device-id", did]
    try:
        r = subprocess.run(cmd, cwd=ENGINE_DIR, env=dict(os.environ),
                           capture_output=True, text=True, timeout=180,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        print("  ⚠️ 执行超时（180s），可能卡在登录/网络请求上")
        return "warn"
    out = (r.stdout or "").strip()
    if r.stderr and r.stderr.strip():
        out += "\n[stderr]\n" + r.stderr.strip()
    print(out[-1500:])
    if "SUCCESS" in out or "签到成功" in out:
        return "ok"
    if "已签到" in out or "重复提交" in out:
        return "warn"
    return "fail"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="只列出账号清单，不执行")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry", action="store_true", help="只探测不提交（默认行为）")
    mode.add_argument("--submit", action="store_true", help="真实提交签到")
    ap.add_argument("--account", default=None, help="只处理指定账号（yml 文件名，不含后缀）")
    args = ap.parse_args()

    accounts = account_files()
    if not accounts:
        print(f"未在 {ACCOUNTS_DIR} 找到任何可用 *.yml 账号配置。")
        print("请参考 accounts/README.md 创建：accounts/<账号名>.yml")
        return 1
    if args.account:
        accounts = [a for a in accounts if a[0] == args.account]
        if not accounts:
            print(f"未找到账号 {args.account!r}，可用账号：{[a[0] for a in account_files()]}")
            return 1

    if args.list:
        print(f"账号配置目录: {ACCOUNTS_DIR}\n共 {len(accounts)} 个账号：")
        for stem, yml_path, did_file in accounts:
            if os.path.exists(did_file):
                did = (open(did_file, encoding="utf-8-sig").read().strip()
                       or "(内容为空，请修复)")
            else:
                did = "(未生成，首次运行自动生成)"
            print(f"  - {stem}   deviceId: {did}")
        return 0

    submit = bool(args.submit)
    print(f"批量{'提交' if submit else '探测（无副作用）'}开始：{len(accounts)} 个账号，账号间隔 {INTERVAL}s")
    stats = {"ok": 0, "warn": 0, "fail": 0}
    for i, (stem, yml_path, did_file) in enumerate(accounts):
        stats[run_one(stem, yml_path, did_file, submit)] += 1
        if i < len(accounts) - 1:
            print(f"  …等待 {INTERVAL}s 错峰（防风控）")
            time.sleep(INTERVAL)
    print("=" * 60)
    print(f"批量完成：成功 {stats['ok']} / 已签或重复 {stats['warn']} / 失败 {stats['fail']}")
    return 0 if stats["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

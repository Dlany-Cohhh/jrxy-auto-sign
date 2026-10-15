#!/usr/bin/env bash
# ============================================================
# 今日校园自动签到 · Linux 一键部署脚本
# 用法: sudo bash deploy/install_linux.sh
# 说明: 创建引擎 venv + Web venv，并可选安装 systemd 服务
# 已适配: Python 3.10~3.12（PyYAML>=6.0 / urllib3==1.26.20 兼容 Python 3.12）
# ============================================================
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="jrxy-web"
INSTALL_PATH="/opt/jrxy-sign"
RUN_USER="www-data"

echo "[1/6] 检查 Python 3.9+ ..."
if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3，请先安装: apt install -y python3 python3-venv python3-pip"
  exit 1
fi
PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "    Python 版本: $PY_VER"
MAJOR="$(echo "$PY_VER" | awk -F. '{print $1}')"
MINOR="$(echo "$PY_VER" | awk -F. '{print $2}')"
if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 9 ]; }; then
  echo "    [错误] 需要 Python 3.9+（当前 $PY_VER），请升级后重试"
  exit 1
fi

echo "[2/6] 安装系统依赖 libgomp1（ddddocr/onnxruntime 运行所需）..."
if command -v apt-get >/dev/null 2>&1; then
  if [ "$(id -u)" -ne 0 ]; then
    sudo apt-get install -y libgomp1 >/dev/null 2>&1 || echo "    [警告] libgomp1 安装失败，可稍后手动: sudo apt-get install -y libgomp1"
  else
    apt-get install -y libgomp1 >/dev/null 2>&1 || echo "    [警告] libgomp1 安装失败，可稍后手动安装"
  fi
else
  echo "    [警告] 未检测到 apt，请自行安装 libgomp（dnf/yum: libgomp；openEuler: gcc-gfortran）"
fi

echo "[3/6] 创建引擎虚拟环境并安装依赖（含 OCR 库，较大，需几分钟）..."
python3 -m venv "$ROOT/jrxy-engine/.venv"
"$ROOT/jrxy-engine/.venv/bin/pip" install --quiet --upgrade pip
# PyYAML>=6.0 与 urllib3==1.26.20 已在 requirements.txt 固定，兼容 Python 3.12
"$ROOT/jrxy-engine/.venv/bin/pip" install --quiet -r "$ROOT/jrxy-engine/requirements.txt"
# 本地验证码识别（默认安装，无需腾讯云密钥；内存 <512MB 的设备请删除下行避免 OOM）
"$ROOT/jrxy-engine/.venv/bin/pip" install --quiet ddddocr

echo "[4/6] 创建 Web 虚拟环境并安装依赖..."
python3 -m venv "$ROOT/web-server/.venv"
"$ROOT/web-server/.venv/bin/pip" install --quiet --upgrade pip
"$ROOT/web-server/.venv/bin/pip" install --quiet -r "$ROOT/web-server/requirements.txt"

echo "[5/6] 可选：安装为 systemd 服务？"
read -r -p "是否安装为 systemd 服务并开机自启? [y/N] " ans
if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
  if [ "$(id -u)" -ne 0 ]; then echo "需要 root 权限安装 systemd 服务，请用 sudo 重跑"; exit 1; fi
  mkdir -p "$INSTALL_PATH"
  # 复制引擎与 Web 服务
  cp -r "$ROOT/jrxy-engine" "$INSTALL_PATH/"
  cp -r "$ROOT/web-server" "$INSTALL_PATH/"
  cp "$ROOT/README-部署手册.md" "$INSTALL_PATH/" 2>/dev/null || true
  # 运行用户：确保 www-data 存在并赋予项目目录权限
  if ! id -u "$RUN_USER" >/dev/null 2>&1; then
    useradd -r -s /usr/sbin/nologin "$RUN_USER"
    echo "    已创建运行用户: $RUN_USER"
  fi
  chown -R "$RUN_USER:$RUN_USER" "$INSTALL_PATH"
  echo "    已授权 $RUN_USER 访问 $INSTALL_PATH"
  # 用绝对路径生成 systemd 单元
  sed "s|/opt/jrxy-sign|$INSTALL_PATH|g" "$ROOT/deploy/jrxy-web.service" > /etc/systemd/system/jrxy-web.service
  systemctl daemon-reload
  systemctl enable jrxy-web
  systemctl start jrxy-web
  echo "systemd 服务已启动: systemctl status jrxy-web"
else
  echo "跳过 systemd 安装。手动启动方式:"
  echo "  cd $ROOT/web-server && .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000"
fi

echo "[6/6] 完成。浏览器打开 http://127.0.0.1:8000"
echo "对外部署请设置环境变量 WEB_TOKEN（见 README-部署手册.md）。"
echo "首次部署后请对照 README-部署手册.md 的《Linux 部署自检清单》逐项核验。"

# 今日校园自动签到 · 服务器部署手册（Windows / Linux / Docker）

把「今日校园自动签到」封装为可部署在 **Windows 或 Linux 服务器** 的 Web 项目，
自带管理界面（状态概览 / 手动签到 / 坐标与定时设置 / 运行日志）与内置定时器。

---

## 目录结构

```
今日校园自动签到/
├── jrxyzuixingnx/            # 签到引擎（核心脚本 + config.yml + .venv，勿改结构）
├── web-server/               # Web 服务（本次新增）
│   ├── app.py                # FastAPI 主程序（API + 内置定时 + 跨平台路径）
│   ├── requirements.txt      # Web 依赖（仅 3 个轻量包）
│   ├── static/index.html     # 管理界面（单文件，零前端构建）
│   └── data/                 # 运行时生成：signlog.db 日志、override.json 坐标覆盖
├── deploy/
│   ├── install_windows.bat   # Windows 一键部署
│   ├── start_windows.bat     # Windows 前台启动
│   ├── install_linux.sh      # Linux 一键部署（可装 systemd 自启）
│   └── jrxy-web.service      # systemd 单元
├── Dockerfile                # Linux 服务器容器化
├── docker-compose.yml        # 容器一键编排
└── auto-sign/                # 已失效原仓库（仅供存档，不参与部署）
```

---

## 快速开始

### 方式一：Windows

```bat
deploy\install_windows.bat
```

完成后启动：

```bat
deploy\start_windows.bat
```

浏览器打开 `http://127.0.0.1:8000`。

开机自启（可选）：用任务计划程序建"启动时运行"任务指向
`deploy\start_windows.bat`（勾选"不管用户是否登录都要运行"），
或用 NSSM 注册为服务：

```bat
nssm install jrxy-sign "E:\A-MyProject\今日校园自动签到\web-server\.venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000
nssm start jrxy-sign
```

### 方式二：Linux（推荐 systemd）

```bash
sudo bash deploy/install_linux.sh
```

脚本会创建两个 venv、安装依赖，并询问是否安装 systemd 服务（回答 y）。
服务名 `jrxy-web`，默认监听 `127.0.0.1:8000`：

```bash
systemctl status jrxy-web
journalctl -u jrxy-web -f      # 看日志
```

不想用 systemd 就手动启动：

```bash
cd web-server
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

> 部署脚本会把项目复制到 `/opt/jrxy-sign`。如果只想原位部署，
> 直接手动执行脚本中 venv 创建与 `pip install` 部分即可。

### 方式三：Docker（Linux 服务器）

```bash
docker compose up -d --build
# 或手动：
docker build -t jrxy-sign .
docker run -d -p 8000:8000 -v jrxy-sign-data:/app/web-server/data jrxy-sign
```

浏览器打开 `http://<服务器IP>:8000`。
容器内引擎配置位于 `/app/jrxyzuixingnx/config.yml`，建议在 compose 中挂载该文件以便维护。

---

## 管理界面功能

| 模块 | 说明 |
|---|---|
| 概览卡片 | 学校、账号（掩码）、任务类型、固定设备 ID、当前坐标、定时状态 |
| 手动操作 | **只探测**（登录+查任务+构造提交体，无副作用） / **立即签到**（真实提交） |
| 坐标与地址 | 修改经纬度（GCJ-02）与定位地址 + **地图选点**（点击/拖拽标记、地址搜索、浏览器定位；高德瓦片 GCJ-02 直接可用，不可达时自动回退 OpenStreetMap 并换算），保存后写 override.json |
| 内置定时 | 启用/停用 + 每日签到时间（默认 22:05），服务器部署后自足运行 |
| 运行日志 | SQLite 持久化，最近 50 条（时间/类型/结果/详情） |

坐标获取：手机高德地图到宿舍楼下**长按**取坐标（GCJ-02，与今日校园一致），
或高德坐标拾取器 `https://lbs.amap.com/tools/picker`。勿用百度地图 BD-09 坐标。

---

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `HOST` | `127.0.0.1` | 监听地址；对外部署设 `0.0.0.0` |
| `PORT` | `8000` | 监听端口 |
| `WEB_TOKEN` | 空 | 设置后所有接口需请求头 `X-Web-Token`，界面会弹出令牌输入 |
| `SIGN_TIME` | `22:05` | 内置定时签到时间（HH:MM，24 小时制） |
| `AUTO_SIGN` | `1` | 内置定时开关（1 启用 / 0 停用） |

Windows 示例（PowerShell）：

```powershell
$env:WEB_TOKEN="change-me-strong-token"
$env:HOST="0.0.0.0"
deploy\start_windows.bat
```

---

## 安全说明（务必阅读）

1. **密码明文**保存在 `jrxyzuixingnx/config.yml`。本界面与接口**永不返回密码**；
   部署到公网前请自行评估风险（建议：仅内网/校园网使用，或加 WEB_TOKEN + 反向代理 HTTPS）。
2. 默认仅监听 `127.0.0.1`，不对外暴露。
3. **设备 ID 勿更换**：账号与设备绑定，更换会触发"疑似更换手机频繁"风控。
   删除 `jrxyzuixingnx/device_id.txt` 会生成新 ID——非必要不要动。
4. 坐标不在签到点半径内会返回"签到失败"，需按上面方法换精确坐标。

---

## 与豆包定时任务的关系

- 本机（Windows）可继续用豆包定时任务「今日校园自动查寝签到」每天 22:05 直接跑引擎；
- 部署到服务器后，用**内置定时**（默认 22:05）即可自足运行；
- 两者同时开启也无害（重复提交会被服务端判为"已签到/重复"，日志正常记录）。

---

## 常见问题

| 现象 | 处理 |
|---|---|
| 页面显示"引擎环境缺失" | 未运行部署脚本，或引擎 venv 被移动：重跑 `install_windows.bat` / `install_linux.sh` |
| 端口被占用 | 换 PORT：`--port 8001`，或关掉占用进程 |
| 签到返回"查寝未开始" | 查寝窗口未开放（通常 22:00 后），定时任务会在窗口内自动跑 |
| 签到返回"签到失败" | 坐标不在半径内，按上文方法换精确坐标 |
| 引擎安装报编译错误 | 确认 Python 3.9+（推荐 3.12）；Linux 缺构建工具时 `apt install -y build-essential` |
| Docker 构建慢 | 首次构建需装 OCR 依赖（约数分钟），之后有缓存 |

---

## 验证方法

```bash
# API 冒烟测试
curl http://127.0.0.1:8000/api/status
curl -X POST http://127.0.0.1:8000/api/run -H "Content-Type: application/json" -d '{"mode":"dry"}'
curl http://127.0.0.1:8000/api/logs
```

带令牌时：

```bash
curl http://127.0.0.1:8000/api/status -H "X-Web-Token: <token>"
```

---

## Linux 部署自检清单

> 用途：首次在 Linux 物理机/云服务器部署后逐项核验。
> 脚本已修复 Python 3.12 依赖兼容（PyYAML>=6.0、urllib3==1.26.20）与 systemd 权限；
> 已在一台 OpenStick（Debian 11 bullseye + Python 3.9.2，512MB 内存）实测通过
> （引擎 dryrun / Web API / 外部访问全部验证），新环境首次部署仍请按本清单逐项核验。

### A. 环境与依赖（部署前）

- [ ] `python3 --version` ≥ 3.9（已实测 Debian 11 bullseye 自带 **3.9.2** 全链路可用；推荐 3.12）
- [ ] `sudo apt-get install -y python3-venv python3-pip build-essential libgomp1`
      （libgomp1 是 ddddocr/onnxruntime 运行必需；CentOS/openEuler 对应 `libgomp`）
- [ ] 网络可访问：pypi.org 或已配置国内 pip 镜像（`pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple`）

### B. 引擎与 Web 依赖安装（部署脚本第 3~4 步）

- [ ] `tencentcloud-sdk-python` 安装版本 ≥ 3.0.351（**3.0.65 已从 PyPI 下架**，requirements 已放宽）
- [ ] 引擎 `.venv/bin/python -c "import yaml, requests, Crypto"` 无报错
- [ ] `pip show PyYAML` 版本 ≥ 6.0（若出现编译错误，确认不是 5.x 版本）
- [ ] Web `.venv/bin/python -c "import fastapi, uvicorn, yaml"` 无报错
- [ ] ddddocr 未安装属正常（懒加载，默认 CLOUD 登录无验证码；**512MB 内存设备勿装，会 OOM**）

### C. 配置（`jrxyzuixingnx/config.yml`）

- [ ] `schoolName` / `username` / `password` / 坐标（GCJ-02）/ `address` 已按实际填写
- [ ] `device_id.txt`（或 `.device_id`）已放置固定设备 ID，**勿重新生成**
- [ ] 坐标在签到点半径内（可用管理界面"地图选点"核验）

### D. 引擎真实链路（无副作用）

- [ ] `./.venv/bin/python -W ignore scripts/dryrun_query_tasks.py`
      输出"登录成功 + 查询到任务"，无 DNS/证书/版本过低报错

### E. Web 服务（systemd 或手动）

- [ ] `systemctl status jrxy-web` 为 `active (running)`，且进程用户为 www-data
- [ ] `curl http://127.0.0.1:8000/api/status` 返回 JSON 且"引擎环境"为正常
- [ ] 页面 `http://<IP>:8000` 可打开（对外部署需 `HOST=0.0.0.0` + WEB_TOKEN）
- [ ] 内置定时：`SIGN_TIME=22:05`、`AUTO_SIGN=1`，服务重启后定时自动生效
- [ ] 日志目录可写：`sudo -u www-data touch web-server/data/.writetest && rm web-server/data/.writetest`

### F. Docker 方式（如采用）

- [ ] `docker compose up -d --build` 构建成功（引擎依赖安装阶段无 PyYAML 编译报错）
- [ ] 容器日志无 `libgomp` 报错；`docker exec <容器> bash -c "cd jrxyzuixingnx && .venv/bin/python scripts/dryrun_query_tasks.py"` 链路通过
- [ ] config.yml 通过卷挂载进容器（不要在镜像里烧录明文密码）

### G. 安全收尾

- [ ] 密码只存在于服务器 `config.yml`；GitHub 仓库中仅有 `config.example.yml`（占位符）
- [ ] 若监听 0.0.0.0：已设置 WEB_TOKEN，或已用 Nginx/Caddy 做 HTTPS 反代

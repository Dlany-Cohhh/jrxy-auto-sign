# 今日校园自动签到 · jrxy-auto-sign

基于今日校园（CPDAILY）协议的**自动查寝/签到引擎 + Web 管理台**，一套代码可部署到
**Windows / Linux / Docker**，支持浏览器式管理界面（状态概览、手动探测/签到、地图选点、
账号设置、内置定时、运行日志）。

> 详细部署步骤、Linux 自检清单与常见问题见 [`README-部署手册.md`](README-部署手册.md)。
>
> **多账号批量签到**（一台服务器跑多个账号，每账号独立 deviceId，无需 Docker）见
> [`README-多账号批量部署.md`](README-多账号批量部署.md)。

---

## 功能特性

- **签到引擎**
  - CAS 统一登录（CLOUD 模式，支持图形验证码 OCR 可选）
  - 查任务 → 组表单 → 动态密钥协商（`getSecretKey`，AES-CBC）→ `first_v4` 提交
  - 三种提交协议：iOS first_v4 / Android 旧版 / 安卓原生全头（App 协议）
  - 固定 `deviceId`（账号↔设备绑定，防风控）
  - 任务类型：0 收集 / 1 签到 / 2 查寝 / 3 政工签到 / 4 教师工作日志

- **Web 管理台**
  - **多账号管理**：账号列表 / 新增 / 编辑 / 删除，账号数据源 = `accounts/` 目录（与批量脚本 `batch_submit.py` 共用）
  - 每账号独立 **deviceId**（可粘贴原绑定 ID 迁移，也可首次运行自动生成），密码绝不回传
  - 单账号操作：**只探测**（登录+查任务+构造提交体，无副作用）/ **立即签到**（真实提交）/ **登录测试**
  - 批量操作：一键探测全部 / 签到全部；内置定时每天批量签到全部账号
  - 从服务器获取签到范围（中心点 + 半径自动写入该账号坐标）
  - 运行日志：SQLite 持久化，Web 端查看最近 200 条
  - **签到结果推送**：按账号独立配置（Server酱 SendKey / 任意 Webhook），手动/批量/定时签到与探测都会推送，通知头部直接标注「签到成功 / 签到失败 / 查寝未开始 / 无任务待签」

- **部署形态**
  - Windows：一键 `install_windows.bat` / `start_windows.bat`
  - Linux：`install_linux.sh` + systemd 自启（`jrxy-web.service`）
  - Docker：`docker compose up -d --build`（Python 3.12-slim）

## 目录结构

```
.
├── jrxy-engine/            # 签到引擎（核心协议实现）
│   ├── actions/              # CAS 登录 / 查任务 / 组表单 / 推送
│   ├── scripts/              # 提交 / 探测 / 密钥工具脚本
│   ├── docs/                 # 协议逆向分析文档
│   ├── config.example.yml    # 配置模板（复制为 config.yml 填写）
│   └── requirements.txt      # 引擎依赖（已适配 Python 3.9+）
├── web-server/               # Web 管理台（FastAPI + 单文件前端）
│   ├── app.py                # 后端 API + 内置定时器
│   └── static/index.html     # 管理界面（深色主题，含地图选点）
├── deploy/                   # 一键部署脚本 + systemd 单元
├── Dockerfile                # 容器化部署
├── docker-compose.yml
└── README-部署手册.md         # 完整部署手册 + Linux 自检清单
```

## 快速开始

### 1. 配置

复制配置模板并填写：

```bash
cp jrxy-engine/config.example.yml jrxy-engine/config.yml
```

关键字段（`config.yml` 已被 `.gitignore` 排除，不会提交到仓库）：

| 字段 | 说明 |
|---|---|
| `schoolName` | 学校全称，须与 `mobile.campushoy.com/v6/config/guest/tenant/list` 返回的 name 一致 |
| `username` / `password` | 学号 / 密码（也可通过 Web 管理台「账号设置」保存） |
| `lon` / `lat` | 坐标（**GCJ-02**，须落在服务端签到点半径内，否则返回"签到失败"） |
| `address` | 定位地址文本（展示用） |
| `type` | 任务类型（默认 2 查寝） |

坐标获取：手机高德地图到楼下长按取坐标，或高德坐标拾取器 `https://lbs.amap.com/tools/picker`
（保留 6 位小数）。**勿用百度地图 BD-09 坐标。**

固定设备 ID：引擎首次运行会生成并保存 `.device_id`，**勿删除/更换**（账号与设备绑定，
更换会触发"疑似更换手机频繁"风控）。

> **多账号**：直接通过 Web 管理台「新增账号」，或复制 `accounts/_example.yml` 创建
> `accounts/<账号名>.yml`（学号/密码/坐标/地址），每个账号独立 deviceId（可粘贴原绑定 ID 迁移，
> 也可首次运行自动生成）。详见 [`README-多账号批量部署.md`](README-多账号批量部署.md)。

### 2. 部署

**Windows：**

```powershell
deploy\install_windows.bat     # 安装依赖
deploy\start_windows.bat       # 启动 Web 服务（默认 127.0.0.1:8000）
```

**Linux（含 systemd 自启）：**

```bash
sudo bash deploy/install_linux.sh
```

**Docker：**

```bash
docker compose up -d --build
```

启动后浏览器打开 `http://127.0.0.1:8000`。

### 3. 命令行直跑引擎（不经 Web）

```bash
# 多账号（推荐）：批量脚本，遍历 accounts/*.yml，deviceId 自动生成或沿用已有绑定
cd jrxy-engine
.venv/bin/python -W ignore scripts/batch_submit.py --list      # 查看账号与设备状态
.venv/bin/python -W ignore scripts/batch_submit.py --dry       # 只探测全部（无副作用）
.venv/bin/python -W ignore scripts/batch_submit.py --submit    # 真实提交全部

# 单账号直跑（--config 指定账号配置文件；不传则回退 config.yml 单账号模式）
.venv/bin/python -W ignore scripts/merged_ios_v4_submit.py --submit \
  --config ../accounts/某账号.yml --variant fkey_cat --protocol ios
```

## Web 管理台 API（多账号版）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/status` | 平台 / 引擎就绪 / 账号数 / 定时状态 |
| GET/POST | `/api/accounts` | 账号列表 / 新建账号 |
| GET/POST/DELETE | `/api/accounts/{name}` | 账号详情 / 更新 / 删除 |
| POST | `/api/accounts/{name}/run` | 单账号 dry 探测 / submit 签到 |
| POST | `/api/accounts/{name}/test-login` | 单账号登录连通测试 |
| POST | `/api/accounts/{name}/sign-range` | 获取并写入该账号签到范围 |
| POST | `/api/accounts/{name}/photo` | 上传该账号签到照片 |
| POST | `/api/batch-run` | 批量探测 / 签到全部账号 |
| POST | `/api/schedule` | 内置定时开关与时间 |
| GET/POST | `/api/logs`、`/api/logs/clear` | 运行日志（SQLite） |

环境变量：`HOST`（默认 127.0.0.1）、`PORT`（默认 8000）、`WEB_TOKEN`（可选，设置后所有接口需
请求头 `X-Web-Token`）、`SIGN_TIME`（内置定时，默认 22:05）、`AUTO_SIGN`（1 启用/0 停用）、
`JRXY_ACCOUNTS_DIR`（账号目录，默认项目根 `accounts/`）。

账号数据源唯一：Web 管理台与批量脚本共用 `accounts/<账号名>.yml` + `<账号名>.device_id`；
密码写入账号 yml 且**永不通过接口回传**。旧的 `config.yml`（单账号）仍被引擎脚本兼容兜底。

## 定时签到

- **内置定时**：Web 服务自带线程定时器（默认每天 22:05，可在页面或 `SIGN_TIME` 调整），
  到点自动**批量签到全部账号**，部署到服务器后自足运行；
- **crontab 直跑**（不开 Web 的小服务器）：`batch_submit.py --submit` 挂 cron，与内置定时**二选一**；
- 重复提交会被服务端判为"已签到/重复"，日志正常记录，不会重复签到。

## 安全说明

- `accounts/`（学号/密码/deviceId）、`web-server/data/`（日志/定时）、`.venv`、引擎
  `config.yml` 与 `.device_id` 均被 `.gitignore` 排除，**不会进入 Git 仓库**
  （仓库内仅有 `accounts/_example.yml` 占位模板）；
- 界面与接口**永不返回密码**；
- 默认仅监听 `127.0.0.1`；对外部署务必设置 `WEB_TOKEN` + HTTPS/反向代理；
- 请遵守学校/校园平台的使用规则，合理控制调用频率，触发风控后需管理员解绑。

## 技术原理

1. **登录**：通过学校 CAS 门户统一认证（CLOUD），拿到会话 Cookie；
2. **查任务**：拉取当前未签到任务（查寝等）与任务详情（是否需照片、签到半径）；
3. **密钥协商**：调用 `getSecretKey` 获取动态密钥（RSA 加密传输 + 交错派生），
   得到提交用的 AES-CBC 密钥（`fkey_cat` 等候选自动尝试）；
4. **提交**：构造表单 → AES 加密 `bodyString` → 按协议（iOS/Android/App）签名提交；
5. **坐标**：GCJ-02 坐标系，与今日校园 App 一致。

协议逆向细节见 `jrxy-engine/docs/`。

## 上游致谢与 License

- 引擎协议实现参考上游项目 [`maserpoassr/jrxy-engine`](https://github.com/maserpoassr/jrxy-engine)（MPL-2.0）；
- 引擎部分沿用上游 MPL-2.0 许可（见 `jrxy-engine/LICENSE`）；本仓库新增的 Web 服务与部署脚本供本项目内使用。

> ⚠️ 本项目仅供学习与个人自动化使用，请勿滥用。使用前请确认你的使用场景符合学校与平台的相关规定。

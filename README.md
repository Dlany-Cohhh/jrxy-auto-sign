# 今日校园自动签到 · jrxy-auto-sign

基于今日校园（CPDAILY / Campusphere）协议逆向的**自动查寝/签到引擎 + 多账号 Web 管理台**。
一套代码可部署到 **Windows / Linux / Docker**，支持：

- **多账号批量签到**：一台服务器跑任意多个账号，每个账号独立绑定 `deviceId`，无需 Docker、无需多实例（小服务器/随身WiFi 也能跑）；
- **浏览器式管理界面**：账号管理、探测/签到、批量操作、内置定时、运行日志、Server酱微信推送；
- **协议级实现**：CAS 登录 → 查任务 → 动态密钥协商（AES-CBC）→ 三种协议提交，解决"今日校园版本过低"。

> 📖 详细部署手册与 Linux 自检清单见 [`README-部署手册.md`](README-部署手册.md)；
> 多账号批量部署与迁移方案见 [`README-多账号批量部署.md`](README-多账号批量部署.md)。

---

## 功能特性

### 签到引擎（`jrxy-engine/`）

- **登录**：CAS 统一登录（CLOUD 模式）；图形验证码可选**本地 OCR（ddddocr）**或腾讯云 OCR；
- **流程**：查任务 → 组表单（含照片上传）→ `getSecretKey` 动态密钥协商 → `first_v4` 签名提交；
- **三种提交协议**：iOS `first_v4` / Android 旧版 / 安卓原生全头（App 协议），自动尝试候选密钥；
- **固定 `deviceId`**：账号 ↔ 设备绑定，防风控（详见下方 [账号与 deviceId](#账号与-deviceid)）；
- **任务类型**：0 收集 / 1 签到 / 2 查寝 / 3 政工签到 / 4 教师工作日志。

### Web 管理台（`web-server/`）

- **多账号管理**：账号列表 / 新增 / 编辑 / 删除；数据源 = `accounts/` 目录（与批量脚本共用）；
- 每账号独立 **deviceId**：可粘贴原绑定 ID 迁移，也可首次运行自动生成；**密码绝不回传**；
- 单账号操作：**只探测**（登录 + 查任务 + 构造提交体，无副作用）/ **立即签到**（真实提交）/ **登录测试**；
- 批量操作：一键探测全部 / 签到全部；
- 内置定时：每天到点自动批量签到全部账号（默认 22:05）；
- 从服务器获取签到范围（中心点 + 半径自动写入该账号坐标）；
- 运行日志：SQLite 持久化，Web 端查看 / 清除。

### 签到结果推送（Server酱）

- 按账号独立配置，可给不同接收人推送；
- 手动签到 / 批量签到 / 内置定时 / **探测**都会推送；
- 通知头部直接标注状态：`【签到成功】` `【签到失败】` `【查寝未开始】` `【无任务待签】`。

### 部署形态

| 形态 | 方式 | 适用 |
|---|---|---|
| Windows | `deploy\install_windows.bat` 一键安装 + `start_windows.bat` 启动 | 本机调试 |
| Linux | `sudo bash deploy/install_linux.sh`，可选 systemd 自启 | 服务器（推荐） |
| Docker | `docker compose up -d --build` | 有 Docker 的环境 |
| 无 Docker 小服务器 | `scripts/batch_submit.py` + cron | 512MB 随身WiFi 等 |

---

## 目录结构

```
.
├── jrxy-engine/                  # 签到引擎（协议逆向实现，MPL-2.0）
│   ├── actions/                  #   登录 / 查任务 / 组表单 / 上传 / 提交
│   ├── scripts/
│   │   ├── merged_ios_v4_submit.py   # 主提交脚本（--config / --dry / --submit）
│   │   ├── batch_submit.py           # 多账号批量（--list / --dry / --submit / --account）
│   │   ├── test_login.py             # 单账号登录连通测试
│   │   ├── fetch_sign_range.py       # 获取签到范围（中心点 + 半径）
│   │   ├── get_fkey_cat.py           # 免登录取 / 派生 AES 密钥
│   │   ├── aes_body.py               # bodyString 加解密工具
│   │   ├── dryrun_query_tasks.py     # 只登录 + 查任务（无副作用）
│   │   └── extract_deviceid.py       # 从 CpdailyInfo 头提取真实 deviceId
│   ├── docs/                      #   协议逆向分析文档
│   ├── config.example.yml         #   配置模板（复制为 config.yml 填写，兼容单账号直跑）
│   └── requirements.txt
├── web-server/                   # Web 管理台（FastAPI + 单文件前端）
│   ├── app.py                    #   全部 API + 内置定时 + Server酱推送
│   ├── static/index.html         #   深色主题管理界面
│   └── requirements.txt          #   fastapi / uvicorn / PyYAML
├── accounts/                     # 多账号数据源（已被 .gitignore 排除，不入库）
│   ├── _example.yml              #   账号模板（_ 开头 = 禁用，不执行）
│   └── <账号名>.yml + <账号名>.device_id
├── deploy/                       # 一键部署脚本 + systemd 单元
│   ├── install_windows.bat / start_windows.bat
│   ├── install_linux.sh
│   └── jrxy-web.service
├── Dockerfile / docker-compose.yml
├── README.md
├── README-部署手册.md            # 完整部署手册 + Linux 自检清单
└── README-多账号批量部署.md      # 多账号批量部署 / 迁移指南
```

---

## 快速开始

### 方式一：Windows（本机体验）

```powershell
# 1. 安装依赖（自动创建引擎与 Web 两个 .venv）
deploy\install_windows.bat

# 2. 启动 Web 管理台
deploy\start_windows.bat          # 默认 http://127.0.0.1:8000
```

### 方式二：Linux 服务器（推荐）

```bash
sudo bash deploy/install_linux.sh
# 脚本会创建两个 venv、安装依赖（含本地 OCR），并可选安装 systemd 服务
```

### 方式三：Docker

```bash
docker compose up -d --build
```

> ⚠️ **Docker 注意**：`accounts/`（账号配置 + deviceId 绑定）默认不持久化，容器重建会丢失账号与绑定。
> 务必额外挂载，例如在 `docker-compose.yml` 增加：
> ```yaml
> volumes:
>   - ./accounts:/app/accounts
>   - jrxy-sign-data:/app/web-server/data
> ```

### 首次使用（任意方式）

1. 打开 Web 管理台 →「**＋ 新增账号**」：
   - 账号名（唯一，不能以 `_` 开头）
   - 学校：输入关键字搜索（内置学校列表）或手动输入全称
   - 学号 / 密码
   - 坐标：可先留空，点「**从服务器获取签到范围**」自动填中心点与半径
2. 列表页点「**探测**」验证登录与任务（无副作用）；
3. 确认无误后点「**签到**」完成一次真实提交。

---

## 账号与 deviceId

### 核心概念

每个账号在 `accounts/` 下对应**一对文件**（Web 与批量脚本共用同一数据源）：

| 文件 | 内容 |
|---|---|
| `<账号名>.yml` | 学号 / 密码 / 学校 / 坐标 / 地址 / 推送配置等（结构与 `config.example.yml` 一致） |
| `<账号名>.device_id` | 该账号**固定**的 deviceId（首次运行自动生成 UUID，之后永久复用） |

以 `_` 开头的文件视为模板 / 禁用，不会被批量脚本执行（如 `_example.yml`）。

### deviceId 绑定规则（重要）

服务端按**「账号 ↔ 设备」**绑定：

- **新账号**（无任何绑定）：首次运行自动生成小写 UUID（与 App 端 `UUID.randomUUID()` 一致），之后固定；
- **已有绑定账号迁移**（真机 / 旧服务器签过到）：把原 deviceId **原样**写进 `<账号名>.device_id`
  （或在 Web 编辑弹窗粘贴），**切勿让其重新生成**；
- **绝不更换 / 共用**：换 deviceId = 换设备，会触发
  `您疑似更换手机频繁，请联系辅导员在辅导猫的手机授权管理功能中为您授权` 风控。

```python
# batch_submit.py 的规则（app.py 相同）：
if 文件存在且非空:  沿用该值（绑定不变）
else:               生成 uuid4 一次，之后永久复用
```

### 坐标要求

- **GCJ-02** 坐标系（与 App 一致），**必须落在服务端签到点半径内**，否则返回"签到失败"；
- 获取方式：手机高德地图到楼下长按取坐标，或高德坐标拾取器 `https://lbs.amap.com/tools/picker`（保留 6 位小数）；
- **勿用百度地图 BD-09 坐标**。

---

## Web 管理台

| 面板 | 功能 |
|---|---|
| 状态概览 | 平台 / 引擎就绪 / 账号数 / 定时状态 |
| 账号列表 | 名称、学校、学号、类型、坐标、deviceId；行内操作：**探测 / 签到**、编辑、删除 |
| 批量操作 | **只探测全部**（dry，无副作用）/ **签到全部**（submit，真实提交） |
| 内置定时 | 启用 + 时间（默认 22:05），到点批量签到全部账号 |
| 运行日志 | SQLite 持久化，最近 200 条，可清除 |

编辑弹窗内：
- 学号留空 = 保持原账号；密码留空 = 保持原密码（**永不回显**）；
- **deviceId**：留空 = 沿用已有或首次自动生成；填值 = 锁定该绑定；
- **消息推送**：方式 `0 禁用` / `2 Server酱/Webhook`，SendKey 填 `SCTxxx` 自动拼地址（见下节）。

---

## 签到结果推送（Server酱）

在账号编辑弹窗的「消息推送」中配置（每个账号可配不同接收人）：

| 项 | 说明 |
|---|---|
| 推送方式 | `2 Server酱/Webhook` |
| SendKey / 推送地址 | 接收人扫码关注「Server酱」公众号获取 SendKey（形如 `SCTxxx`），填写后自动拼为 `https://sctapi.ftqq.com/<SendKey>.send`；也可直接填完整 Webhook 地址 |
| 触发时机 | 手动签到 / 批量签到 / 内置定时 / **探测**均推送 |
| 通知标题 | 头部直接标注：`【签到成功】` / `【签到失败】` / `【查寝未开始】` / `【无任务待签】`（探测场景为「探测成功/失败」） |
| 额度 | Server酱免费 5 条/天，账号多 + 频繁探测时注意控制 |

> 推送由 Web 层实现（`app.py` 的 `send_notify`，标准库 urllib，无额外依赖）。
> WxPusher 公众号渠道已被微信限制（受理但无法送达），已从本项目移除。

---

## 命令行批量签到（不经 Web）

小服务器（无 Docker / 不开 Web）推荐直接挂 cron：

```bash
cd jrxy-engine
# 查看账号清单与 deviceId 状态（不联网）
.venv/bin/python -W ignore scripts/batch_submit.py --list

# 只探测全部账号（登录 + 查任务 + 构造提交体，无副作用）
.venv/bin/python -W ignore scripts/batch_submit.py --dry

# 真实提交全部账号（会消耗签到机会）
.venv/bin/python -W ignore scripts/batch_submit.py --submit

# 只处理单个账号（按 yml 文件名，不含 .yml）
.venv/bin/python -W ignore scripts/batch_submit.py --submit --account alice
```

单账号直跑（指定配置文件；不传 `--config` 则回退旧 `config.yml` 模式）：

```bash
.venv/bin/python -W ignore scripts/merged_ios_v4_submit.py --submit \
  --config ../accounts/alice.yml --variant fkey_cat --protocol ios
```

### 批量脚本环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `JRXY_ACCOUNTS_DIR` | `<项目根>/accounts` | 账号配置目录 |
| `JRXY_INTERVAL` | `10` | 每账号间隔秒数（错峰防风控） |
| `JRXY_PYTHON` | 自动探测 `.venv` | 引擎解释器路径 |

### 定时（二选一，勿双开）

- **Web 内置定时**：管理台开启即可（默认每天 22:05）；
- **crontab 直跑**（不开 Web）：

```bash
20 22 * * * /opt/jrxy-sign/jrxy-engine/.venv/bin/python /opt/jrxy-sign/jrxy-engine/scripts/batch_submit.py --submit >> /var/log/jrxy-batch.log 2>&1
```

重复提交会被服务端判为"已签到/重复"，日志正常记录，不会重复签到。

---

## Web 管理台 API

所有接口（除 `/` 与 `/school_list.js` 静态页外）在设置了 `WEB_TOKEN` 时均需请求头 `X-Web-Token`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/status` | 平台 / 引擎就绪 / 账号数 / 定时状态 |
| GET/POST | `/api/accounts` | 账号列表 / 新建账号 |
| GET/POST/DELETE | `/api/accounts/{name}` | 账号详情 / 更新 / 删除 |
| POST | `/api/accounts/{name}/run` | 单账号 `{"mode":"dry"}` 探测 / `{"mode":"submit"}` 签到 |
| POST | `/api/accounts/{name}/test-login` | 单账号登录连通测试 |
| POST | `/api/accounts/{name}/sign-range` | 获取并写入该账号签到范围 |
| POST | `/api/accounts/{name}/photo` | 上传该账号签到照片 |
| POST | `/api/batch-run` | 批量 `dry` 探测 / `submit` 签到全部账号 |
| POST | `/api/schedule` | 内置定时开关与时间 |
| GET/POST | `/api/logs`、`/api/logs/clear` | 运行日志（SQLite） |

---

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `HOST` | `127.0.0.1` | 监听地址（对外部署设 `0.0.0.0`） |
| `PORT` | `8000` | 监听端口 |
| `WEB_TOKEN` | 空 | 设置后所有 API 需请求头 `X-Web-Token`（对外部署**必设**） |
| `SIGN_TIME` | `22:05` | 内置定时签到时间（HH:MM） |
| `AUTO_SIGN` | `1` | 内置定时开关（1 启用 / 0 停用） |
| `JRXY_ACCOUNTS_DIR` | `<项目根>/accounts` | 账号配置目录 |

---

## 安全说明

- `accounts/`（学号/密码/deviceId）、`web-server/data/`（日志/定时）、`.venv`、
  引擎 `config.yml` 与 `.device_id`、签到照片、日志均被 `.gitignore` 排除，**不会进入 Git 仓库**
  （仓库内仅有 `accounts/_example.yml` 占位模板）；
- 界面与接口**永不返回密码**；
- 默认仅监听 `127.0.0.1`；对外部署务必设置 `WEB_TOKEN` + HTTPS / 反向代理；
- 遵守学校/校园平台的使用规则，合理控制调用频率。

---

## 技术原理

1. **登录**：通过学校 CAS 门户统一认证（CLOUD），拿到会话 Cookie；
2. **查任务**：拉取当前未签到任务与详情（是否需照片、签到半径）；
3. **密钥协商**：调用 `getSecretKey` 获取动态密钥（RSA 加密传输 + 交错派生），得到提交用 AES-CBC 密钥（`fkey_cat` 等候选自动尝试）；
4. **提交**：构造表单 → AES 加密 `bodyString` → 按协议（iOS / Android / App）签名提交；
5. **坐标**：GCJ-02 坐标系，与今日校园 App 一致。

> 「今日校园版本过低」实为服务端解不开请求体（密钥不对）的伪装报错；`fkey_cat = hru89rwr87Nthifa`
> 为当前全平台固定密钥（见 `jrxy-engine/README.md`，`scripts/get_fkey_cat.py` 可在线获取以防轮换）。
> 完整协议逆向报告见 `jrxy-engine/docs/请求加密接口_逆向分析.md`。

---

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| `版本过低` | 提交密钥不对。用 `scripts/get_fkey_cat.py` 刷新密钥，或确认 `--variant fkey_cat --protocol ios` |
| `您疑似更换手机频繁` | deviceId 被更换。换回该账号此前绑定的 ID；必要时请辅导员在辅导猫中解绑授权 |
| `签到失败` | 坐标不在签到点半径内。用高德取 GCJ-02 坐标，或用「从服务器获取签到范围」自动填充 |
| `验证码识别出现问题` | 本地未装 OCR。`pip install ddddocr`（内存 <512MB 的设备慎装，可改用腾讯云 OCR 填 `config.yml` 的 `ocrOption`） |
| 查寝未开始 / 无任务 | 服务端正常响应（窗口外 / 已签），不是错误 |
| 内存不足（小服务器） | 不要装 ddddocr/onnxruntime；只跑 `batch_submit.py` + cron，峰值 50~150MB |

---

## 许可证与免责声明

- 引擎协议实现参考上游 [`maserpoassr/jrxy-engine`](https://github.com/maserpoassr/jrxy-engine)（MPL-2.0），见 `jrxy-engine/LICENSE`；
- 本仓库新增的 Web 服务与部署脚本供本项目内使用。

> ⚠️ 本项目仅供学习与个人自动化使用。虚拟定位、代签到可能违反校规与平台条款，请在使用前确认你的场景符合学校与平台的相关规定。

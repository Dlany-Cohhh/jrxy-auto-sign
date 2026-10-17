# 多账号批量签到 · 部署指南

本项目原本是**单账号**设计：引擎提交脚本只读取 `config.yml` 里的第一个用户（`users[0]`），`device_id` 也是引擎根目录的单一文件。要在**一台服务器上批量运行多个账号**，本指南给出不改引擎协议、只加两个脚本文件的方案，适用于小服务器（无需 Docker、无需 Web 服务，只需 cron + 一个 venv）。

## 改动清单（本仓库已包含）

| 文件 | 说明 |
|---|---|
| `jrxy-engine/scripts/merged_ios_v4_submit.py` | 新增 `--config <路径>` 参数：指定账号配置文件（向后兼容，不传时仍用 `config.yml`） |
| `jrxy-engine/scripts/batch_submit.py` | 批量脚本：遍历 `accounts/*.yml`，逐个账号执行提交/探测 |
| `accounts/_example.yml` | 每账号配置模板（`_` 开头 = 禁用，不会被执行） |
| `accounts/README.md` | 账号目录使用说明（新账号/迁移/照片/定时/风控） |
| `.gitignore` | 新增 `accounts/` 排除项（学号/密码/deviceId 不入库） |

## 为什么需要每账号独立 deviceId

服务端按**"账号 ↔ 设备"**绑定（见 `merged_ios_v4_submit.py` 设备 ID 逻辑与 README 风控说明）：

- 换 deviceId = 换设备，触发"更换手机频繁/设备未授权"风控，需管理员解绑；
- 因此**每个账号必须有一个独立且固定不变的 deviceId**，账号之间绝不共用。

批量脚本的规则：

```python
if os.path.exists(did_file):        # 文件已存在 → 沿用（绑定不变）
    return 读出的值
else:                               # 不存在 → 只生成一次，之后永久复用
    did = str(uuid.uuid4())         # 小写带连字符，与 App 端 UUID.randomUUID() 一致
```

## 快速上手（新账号）

```bash
# 1. 建账号配置（复制模板）
cp accounts/_example.yml accounts/alice.yml
#    填入 学校全称/学号/密码/坐标/地址（坐标须 GCJ-02 且落在签到半径内）

# 2. 先探测这一个账号（无副作用）
python scripts/batch_submit.py --dry --account alice

# 3. 真实提交一次，确认无风控提示
python scripts/batch_submit.py --submit --account alice

# 4. 全部账号一起跑（deviceId 自动逐个生成）
python scripts/batch_submit.py --submit
```

## 已有绑定账号的迁移（两台服务器合并场景）

两个账号已在各自服务器上跑通并绑定设备 ID，合并到一台服务器时：

```bash
# 服务器A 上取账号A的绑定 ID
cat /opt/jrxy/jrxy-engine/.device_id        # 或 device_id.txt
# 服务器B 上取账号B的绑定 ID

# 目标服务器上，把原 ID【原样】写入对应账号文件（文件存在即锁死，脚本不会重新生成）
cp /opt/jrxy/jrxy-engine/.device_id /opt/jrxy/accounts/a.device_id
scp root@服务器B:/opt/jrxy/jrxy-engine/.device_id /opt/jrxy/accounts/b.device_id

# 校验：两个 ID 都存在、互不相同
cat accounts/*.device_id
```

同时把两台的 `config.yml` 用户段（含 Web 端 `credentials.json` 里的账号密码，若有）合并成各自的 `accounts/a.yml`、`accounts/b.yml`。

**迁移后必须停掉旧服务器的签到**（`AUTO_SIGN=0` 或停服务/容器），否则同一账号从两个出口同时签到属异常行为。

## 定时批量（Linux）

```bash
# crontab -e（服务器时区注意与签到窗口一致）
20 22 * * * /opt/jrxy/jrxy-engine/.venv/bin/python /opt/jrxy/jrxy-engine/scripts/batch_submit.py --submit >> /var/log/jrxy-batch.log 2>&1
```

Windows（本机调试/部署）：任务计划程序，操作填：

```
程序:  E:\A-MyProject\今日校园自动签到\jrxy-engine\.venv\Scripts\python.exe
参数:  -W ignore E:\A-MyProject\今日校园自动签到\jrxy-engine\scripts\batch_submit.py --submit
起始于: E:\A-MyProject\今日校园自动签到
```

可选环境变量：`JRXY_ACCOUNTS_DIR`（账号目录）、`JRXY_INTERVAL`（账号间隔秒，默认 10，防风控）、`JRXY_PYTHON`（引擎解释器路径，默认自动探测 `.venv`）。

## 验证与排错

1. `python scripts/batch_submit.py --list` —— 列出账号与 deviceId 状态；
2. `--dry` 逐账号：登录 + 查任务 + 领密钥 + 构造提交体（不提交）；注意无任务时会在"查任务"阶段退出，验不到设备绑定；
3. 最稳验证 = 任务窗口内 `--submit` 真跑一次，看日志有无"更换手机频繁/设备未授权"；
4. 批量汇总：`批量完成：成功 N / 已签或重复 M / 失败 K`，失败的账号去查对应日志段。

## 风控红线

1. deviceId 每账号独立固定，账号间不共用、不删改；
2. 批量默认错峰 10 秒；多台机器/多方案（Web 与批量）不要给同一账号用两个 ID；
3. 若触发风控：换回该账号此前绑定的 ID，必要时管理员解绑；
4. 遵守学校与平台使用规则，合理控制频率。

---

# Web 管理台（多账号版）

`web-server/` 已同步改造为多账号管理台，**与 `accounts/` 目录共用一套账号配置**（与 `batch_submit.py` 完全一致）：Web 页面新增的账号，批量脚本直接可见、可直接跑；反之亦然。

## 多账号改造内容

| 文件 | 说明 |
|---|---|
| `web-server/app.py` | 重写为多账号版：账号列表/新增/编辑/删除（读写 `accounts/*.yml` 与 `*.device_id`）、单账号探测/签到、批量运行（复用 `batch_submit.py`）、内置定时（每天批量签到全部账号）、登录测试、签到范围获取、照片上传 |
| `web-server/static/index.html` | 重写为多账号界面：账号列表 + 编辑弹窗（含 deviceId 迁移填框）、批量操作、定时、日志 |
| `jrxy-engine/scripts/test_login.py` | 新增 `--config` 参数（按账号测试登录） |
| `jrxy-engine/scripts/fetch_sign_range.py` | 新增 `--config` 参数（按账号获取签到范围） |

## 启动

```bash
# Windows（本机）
cd web-server
.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000

# Linux 服务器
cd web-server
.venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

首次启动前安装依赖（只需一次）：

```bash
cd web-server
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
.venv/bin/pip install -r requirements.txt       # Linux
```

环境变量：`HOST`（默认 127.0.0.1）、`PORT`（默认 8000）、`WEB_TOKEN`（对外部署必设）、`SIGN_TIME`（定时时间，默认 22:05）、`AUTO_SIGN`（1/0，默认 1）、`JRXY_ACCOUNTS_DIR`（账号目录，默认项目根 `accounts/`）。

## 页面用法

1. **新增账号**：账号名 / 学校（关键字搜索或手输）/ 学号 / 密码 / 坐标（可点「从服务器获取签到范围」自动填中心点与半径）/ 可选照片与代理。
2. **已有绑定账号迁移**：在账号弹窗的 **deviceId** 一栏**原样粘贴**该账号已绑定的 ID（页面留空 = 沿用或首次运行自动生成，**切勿让已绑定账号重新生成**）。
3. **单账号**：列表行内「探测 / 签到」；**批量**：面板「只探测全部 / 签到全部」。
4. **定时**：启用后每天到点批量签到全部账号（即 Web 内置版 cron，与 Linux crontab 二选一，别两个都开）。

## 与批量脚本的关系

- 数据源唯一：`accounts/<name>.yml` + `<name>.device_id`，Web 与 `batch_submit.py` 双向共用；
- Web 日志与引擎日志独立：Web 记录在 `web-server/data/signlog.db`（运行时自动生成，已入 .gitignore）；
- 照片上传后存到 `accounts/<name>/<文件名>`，账号配置里的 `photo` 自动写为 `../accounts/<name>/<文件名>`（引擎按 `jrxy-engine/` 根解析）。

## Web 接口一览（均需 X-Web-Token，若设置了 WEB_TOKEN）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/status` | 平台/引擎就绪/账号数/定时状态 |
| GET/POST | `/api/accounts` | 账号列表 / 新建账号 |
| GET/POST/DELETE | `/api/accounts/{name}` | 账号详情 / 更新 / 删除 |
| POST | `/api/accounts/{name}/run` | 单账号 dry 探测或 submit 签到 |
| POST | `/api/accounts/{name}/test-login` | 单账号登录连通测试 |
| POST | `/api/accounts/{name}/sign-range` | 获取并写入该账号签到范围 |
| POST | `/api/accounts/{name}/photo` | 上传该账号签到照片 |
| POST | `/api/batch-run` | 批量探测/签到全部账号 |
| POST | `/api/schedule` | 内置定时开关与时间 |
| GET/POST | `/api/logs`、`/api/logs/clear` | 运行日志 |

> 与小服务器方案的关系：引擎仍是 CLI（跑完即退、峰值 ~50-150MB），Web 只是可选的便捷管理界面；不开 Web 完全不影响批量签到与 crontab。

## 签到结果推送（Server酱，按账号独立配置）

在账号编辑弹窗的「消息推送」里配置（每个账号可配不同接收人）：

| 项 | 说明 |
|---|---|
| 推送方式 | `0 禁用` / `2 Server酱（服务号）` |
| SendKey / 推送地址 | 接收人扫码关注「Server酱」公众号后获得 SendKey（形如 `SCTxxx`），填入后系统自动拼为 `https://sctapi.ftqq.com/<SendKey>.send`；也可直接填完整 Webhook 地址 |
| 触发时机 | 手动签到 / 批量签到 / 内置定时都会推送；**探测（dry）也会推送**（便于验证新账号是否绑定成功） |
| 通知标题 | 头部直接标注状态：`【签到成功】` / `【签到失败】` / `【查寝未开始】` / `【无任务待签】`（探测场景为「探测成功/失败」） |
| 额度 | Server酱免费 5 条/天；账号多 + 频繁探测时注意控制，避免超额度 |

> 说明：推送由 Web 层实现（`app.py` 的 `send_notify`，标准库 urllib，无额外依赖），不依赖引擎；
> `WxPusher` 公众号渠道已被微信限制（推送受理但无法送达微信），已从本项目移除。


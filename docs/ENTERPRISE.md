# 企业内部部署与运维

本版本面向单企业约 100 个员工账号，采用 React + FastAPI + PostgreSQL + 多 worker。图片与模型预览保存在共享持久卷。费用管理不在本次范围内。

## 账号与项目权限

- 管理员创建、停用员工账号及重置密码；初始密码至少 12 个字符，首次登录强制更换。
- 登录使用服务端可撤销的随机会话，Cookie 为 HttpOnly、SameSite=Lax，生产启用 Secure。修改请求校验来源与 CSRF token。
- 项目默认仅负责人可见。负责人添加编辑者、查看者，或转交项目；转交后原负责人成为编辑者。
- 编辑者可提交、继续、取消任务；查看者可浏览和下载。只有负责人能删除项目。
- 管理员身份不自动授予业务项目访问权。员工离职后可由服务器操作员使用 `transfer-projects` 转交其项目。
- 图片、下载、会话与实时事件均检查成员权限。停用账号或撤销成员后，实时连接会在下一个轮询周期关闭；正在执行的任务在下一次决策/图像提交前重新检查员工权限。
- 操作审计保存账号管理、登录、成员变更、项目创建/删除和转交记录，不保存密码或完整请求正文。

## 生产部署

准备一台能够访问模型供应商的 Linux 服务器，安装 Docker Engine 与 Compose。准备可用的域名和 TLS 证书，开放企业规定的访问入口。模型计算在供应商侧执行；本机 OCR 若启用，需另外评估资源。

```sh
cp .env.production.example .env.production
# 编辑数据库密码、DATABASE_URL、域名、APP_ORIGIN 和模型密钥。
docker compose --env-file .env.production up -d --build
docker compose --env-file .env.production exec api python -m app.manage create-admin admin
```

创建管理员时交互输入密码，密码不会出现在命令参数中。首次登录仍需修改初始密码。之后通过页面右上角“员工管理”创建员工，通过“项目成员”共享项目。

`POSTGRES_PASSWORD` 建议使用随机十六进制字符串，并在 `DATABASE_URL` 使用相同密码；如果使用其他字符，应按 URL 规则编码。真实环境文件不提交到 Git。生产模式拒绝 SQLite、HTTP APP_ORIGIN 或未启用 Secure Cookie 的配置。

Caddy 提供入口及流式转发，默认尝试为域名申请证书。纯内网域名需要在 `deploy/Caddyfile` 配置企业证书，或使用 `tls internal` 并将对应 CA 分发到员工设备。已有企业反向代理时，也可由该代理终止 TLS，再转发到 API；APP_ORIGIN 始终填写员工实际访问的 HTTPS 地址。

默认仅网关发布 80/443，数据库和 API 不直接发布端口。可通过 BIND_ADDRESS、HTTP_PORT、HTTPS_PORT 调整绑定。应用以非 root 用户运行，容器根文件系统只读，图片卷和临时目录可写。PostgreSQL、图片、网关证书分别保存在命名卷。

## 任务并发与故障恢复

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| WORKER_CONCURRENCY | 3 | 每个 worker 进程同时执行的任务数 |
| GLOBAL_CONCURRENCY | 5 | 所有 worker 合计运行上限 |
| USER_CONCURRENCY | 2 | 单员工运行上限 |
| WORKER_LEASE_SECONDS | 60 | 任务执行租约，约每四分之一个周期续期 |
| WORKER_SHUTDOWN_SECONDS | 45 | 优雅停止等待时间 |
| DB_POOL_SIZE | 20 | 每个进程的业务连接池上限，至少为进程并发数加 4 |
| SSE_POLL_SECONDS | 2 | 实时事件与权限复核周期 |
| SESSION_TTL_SECONDS | 28800 | 登录有效期，默认 8 小时 |

```sh
docker compose --env-file .env.production up -d --scale worker=2
```

所有 worker 使用同一份配置。领取任务通过数据库事务协调全局与员工上限，按员工最近获得执行机会的时间分配任务。同一项目只允许一个排队、执行或等待回答的任务，防止持续会话混入并发修改。

worker 定期写入心跳；仅回收租约过期且项目执行锁已释放的运行任务。业务写入和检查点提交均校验执行租约，失效执行者不能继续提交结果。等待回答会保存检查点并释放执行容量，回答后重新排队。

远端图像提交已记录但响应未知时，保留 unknown 状态，不自动重复提交。这个机制无法替代供应商侧幂等接口，也不能承诺外部请求严格执行一次。停止 worker 会先等待当前任务，超时取消后保留可恢复的检查点和工具记录。

当前支持同一服务器上共享持久卷的多个容器。跨服务器部署需要另外提供可靠的共享文件系统或实现对象存储适配；本版本没有实现跨服务器高可用。并发默认值是起点，实际生成吞吐量仍受供应商配额和延迟影响。

## 健康、日志与日常检查

```sh
docker compose --env-file .env.production ps
docker compose --env-file .env.production logs --tail 100 api worker
```

- `/api/health/live`：API 进程存活。
- `/api/health/ready`：数据库可用；供网关与容器探测。
- worker 容器健康检查：验证本容器最近 30 秒的心跳。
- 登录后 `/api/health`：模型配置状态。
- 管理员 `/api/admin/operations`：任务状态计数、最长排队时间、活跃 worker 与近期审计。

API 日志包含请求编号、方法、路径、状态码与耗时，不包含请求正文和 Cookie；worker 记录启动及执行异常。容器日志按每份 10MB、最多 5 份轮换。将容器不健康、worker 心跳中断、排队时间和任务失败率接入企业已有监控。

## 一致性备份与恢复

从有 Python 3.11+ 和 Docker CLI 的运维主机运行，工作目录为仓库根目录：

```sh
python scripts/backup.py backup /secure-backups/design-2026-11-06 --env-file .env.production
python scripts/backup.py verify /secure-backups/design-2026-11-06
```

备份会暂时停止 API 和所有 worker，使数据库与图片保持一致；数据库继续运行。输出包括 PostgreSQL 自定义格式备份、图片及预览压缩包、SHA-256 校验清单。成功后恢复原先运行的服务；失败时保持服务停止，避免半完成的恢复对外提供服务。备份目录需要尚不存在。

恢复会替换目标部署的数据库及图片，先在独立环境演练：

```sh
python scripts/backup.py restore /secure-backups/design-2026-11-06 \
  --env-file .env.production --confirm-replace-data
```

脚本先验证完整性，再停止 API/worker，使用与服务器相同版本的 pg_dump/pg_restore，并恢复图片。归档只允许 assets/previews 下的普通文件与目录，拒绝路径穿越与链接。恢复失败时检查错误再手动启动服务。

备份包含员工信息、登录会话和设计资料，保存到企业受控的独立备份位置。建议由现有备份平台定期调度，并定期在隔离环境验证项目、图片与登录。

## 从个人版本迁移

### 保留 SQLite 进行本地开发

升级前先运行 `scripts/stop.ps1` 停止旧服务。升级代码、安装依赖后，创建管理员并显式接管尚无负责人的历史项目：

```powershell
.\.venv\Scripts\python.exe -m app.manage create-admin admin
.\.venv\Scripts\python.exe -m app.manage adopt-unowned admin
.\scripts\start.ps1
```

本地默认 `APP_ORIGIN=http://127.0.0.1:8000`、`COOKIE_SECURE=false`。使用 Vite 开发页面时，将 APP_ORIGIN 配置为 Vite 实际地址并重启后端。

### 迁移到 PostgreSQL

1. 完成或取消旧系统全部排队、执行和等待回答任务，停止旧系统，备份整个旧 data 目录。旧版 session 表结构需先按正常启动流程完成升级。
2. 启动新的 Compose 部署并创建目标管理员，保持目标业务数据为空。停止新系统 API/worker，数据库保持运行。
3. 将旧 data 只读挂载到一次性迁移容器，指定历史项目的新负责人：

```sh
docker compose --env-file .env.production stop api worker
docker compose --env-file .env.production run --rm --no-deps \
  -v /absolute/legacy-data:/legacy:ro api \
  python -m app.migrate --source /legacy --owner admin
docker compose --env-file .env.production start api worker
```

迁移保留项目、session ID、事件顺序、对话、工具记录、图片与版本关系，并校验被引用文件的摘要。源目录不会修改。历史操作者未知时保留为空，不伪造员工身份。已完成任务的旧控制检查点不迁移；后续对话由完整 session journal 重建。

迁移工具拒绝有未完成任务的源库、有业务记录或图片目录的目标，使用数据库事务导入。迁移期间不要开放新系统使用；跨数据库与文件系统无法形成一个物理事务，异常退出后应检查目标并从源备份重新执行，确认前保留旧系统。

### 离职项目转交与密码恢复

```sh
docker compose --env-file .env.production exec api python -m app.manage reset-password employee
docker compose --env-file .env.production exec api python -m app.manage transfer-projects successor --from-user employee
```

这些命令仅供有服务器访问权的操作员使用，操作会记录审计。

## 发布、回滚与验证

发布前创建一致性备份，设置可追溯的 APP_VERSION，构建并重新启动服务。数据库结构采用向前兼容的增量建表/加列，生产启动会串行协调初始化。涉及不兼容的数据变更时，回滚需恢复匹配版本的数据库与图片备份。

```sh
docker compose --env-file .env.production up -d --build
```

后端测试默认使用临时 SQLite；设置 TEST_DATABASE_URL 后，还会在该独立 PostgreSQL 测试库验证存储、权限、队列和迁移。测试会清空测试库业务表，必须使用可丢弃且库名以 `_test` 结尾的测试库。

```powershell
$env:TEST_DATABASE_URL='postgresql://postgres@127.0.0.1:55433/design_agent_test'
.\.venv\Scripts\python.exe -m pytest backend/tests -q
npm --prefix frontend test
npm --prefix frontend run build
.\.venv\Scripts\python.exe scripts/verify_session_workspace.py
```

页面回归使用独立数据目录和本地模型替身，覆盖登录、员工创建、成员权限、查看者只读、权限实时撤销，以及生成、编辑、追问、删除和移动布局，不调用付费 API。生产验收还需按实际配额进行生成并发测试。

企业资料仍会发送至当前配置的 DeepSeek、Seedream 服务。企业应明确允许发送的资料范围；内网部署不改变这一数据流向。

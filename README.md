# DBShooter

Web 版多数据库管理工具(DBeaver 核心体验):多连接管理 · 多 Tab SQL 工作台 · Text-to-SQL AI 助手。

## 一期功能

- **五类数据库**:SQLite / MySQL / PostgreSQL / Redis / MongoDB,驱动层可插拔(新增数据库 = 加一个 driver 文件)
- **连接管理**:增删改查、测试连接、只读模式;密码 **Fernet 加密**存内置 SQLite
- **SQL 工作台**:多 Tab 编辑器(CodeMirror 高亮)、WebSocket 流式执行、结果分页加载、执行计划、查询历史、CSV 导出、查询取消
- **Redis**:键空间 SCAN 浏览(禁 `KEYS *`)、按类型(string/hash/list/set/zset)值视图、命令行执行
- **MongoDB**:`db.coll.find({...}).sort().limit()` 查询语法、文档 JSON 视图
- **AI 助手**:Text-to-SQL,OpenAI 兼容 Provider(**多配置、单生效、面板内即点即切**),schema 上下文注入,流式输出,会话持久化;生成 SQL 先入编辑器确认,不直接执行
- **工作区持久化**:页签与 SQL 内容自动保存,重开自动恢复
- **暗/亮双主题**自由切换

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12 · FastAPI · aiosqlite / aiomysql / asyncpg / redis-py / motor |
| 前端 | Vue 3 · TypeScript · Vite · Pinia · CodeMirror 6(IDE 界面为自绘组件,设计系统见 `docs/prototype.html`) |
| 存储 | 项目内置 SQLite(连接 / AI Provider / AI 会话 / 查询历史 / 工作区页签 / settings) |
| 部署 | Docker 单镜像,前端构建产物由 FastAPI 托管 |

## 目录结构

```
├── run.py                  # 服务入口(python run.py)
├── pyproject.toml          # pip install . 后获得 dbs 命令
├── requirements.txt        # 后端依赖(dev 见 requirements-dev.txt)
├── pytest.ini
├── Dockerfile              # 单镜像:多阶段构建
├── backend/
│   ├── app/
│   │   ├── main.py         # FastAPI 装配:REST + WS + 静态托管 + 令牌鉴权
│   │   ├── config.py       # 数据目录/密钥/前端产物路径(环境变量可覆盖)
│   │   ├── db.py           # 内置 SQLite 存储层
│   │   ├── security.py     # Fernet 加解密
│   │   ├── drivers/        # 驱动层:base + sqlite/mysql/pg/redis/mongo
│   │   ├── services/       # 连接池管理 / 查询会话 / AI 服务
│   │   └── api/            # REST 路由(仅 GET/POST)+ WebSocket
│   ├── cli/                # dbs 命令行客户端(REST/WS 薄客户端)
│   └── tests/              # pytest
├── frontend/
│   ├── src/                # components / stores / api / styles
│   └── tests/              # vitest(17 个用例)
└── docs/                   # 需求分析 + 可交互原型(含标注模式)
```

## 本地开发

```bash
# 后端(需要 Python 3.12)
py -3.12 -m venv .venv          # 或 python3.12 -m venv .venv
.venv\Scripts\activate          # Windows;Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
python run.py                   # http://127.0.0.1:5718

# 前端(另开一个终端)
cd frontend
npm install
npm run dev                     # http://127.0.0.1:5173,已代理 /api 与 /ws 到 5718
```

开发热重载:`DBSHOOTER_DEV=1 python run.py`。

## 命令行(CLI)

`dbs` 是现有 REST/WS 接口的薄客户端(方案见 `docs/02-CLI设计方案.md`),适合脚本与管道场景:

```bash
# 仓库内直接用(pip install . 后获得 dbs 命令;Docker 镜像内已装好,用 docker exec <容器> dbs ...)
python -m backend.cli health
python -m backend.cli conn add --name 本地 --type sqlite --param path=/tmp/a.db
python -m backend.cli tree 本地 --depth 2
python -m backend.cli query 本地 "select * from users"            # TTY 表格;管道默认 CSV
python -m backend.cli query 本地 --stdin < a.sql
python -m backend.cli export 本地 "select * from big" -o big.csv  # 大结果集流式落盘
python -m backend.cli ai ask 本地 "统计每个城市的用户数"
python -m backend.cli ai ask 本地 "..." --sql | python -m backend.cli query 本地 --stdin
```

全局选项(写在子命令之前):`-s/--server`(env `DBSHOOTER_URL`)、`-t/--token`(env `DBSHOOTER_TOKEN`)、`--timeout`、`--no-color`。
退出码:`0` 成功 / `1` 业务失败 / `2` 用法错误 / `3` 连不上服务 / `4` 未授权。

## 测试与类型检查

```bash
pytest -q                                # 后端
pyright                                  # 后端类型检查(配置见 pyrightconfig.json,须 0 error)
cd frontend && npm run test:run          # 前端
cd frontend && npm run typecheck         # 前端 vue-tsc
```

## Docker 部署(单镜像,前端由后端托管)

```bash
docker build -t dbshooter .
docker run -d -p 5718:5718 -v dbshooter-data:/data \
  -e DBSHOOTER_SECRET=<随机字符串> -e DBSHOOTER_TOKEN=<随机令牌> dbshooter
# 打开 http://localhost:5718
docker exec -it <容器> dbs conn list          # 镜像内自带 dbs 命令
```

- 容器内以非 root 用户 `appuser`(UID 1000)运行,`/data` 已 chown 给它;用**宿主目录**挂载
  `/data`(而非命名卷)时,需要 `chown 1000:1000` 该目录,否则非 root 进程写不了元数据库。
- 容器内 `DBSHOOTER_HOST=0.0.0.0`(由 Dockerfile 设置)——`run.py` 默认只听 127.0.0.1,
  端口映射要生效必须显式放开;既然对外监听,请一并设置 `DBSHOOTER_TOKEN`。
- **反向代理必须透传原始 Host 头**:`/ws` 会校验浏览器 `Origin` 与 `Host` 同源(防跨站 WS),
  代理改写 Host 会让浏览器的 WS 被以 4403 关闭(CLI 不带 Origin,不受影响)。nginx 至少要写
  `proxy_set_header Host $host;` 并透传 `Upgrade`/`Connection` 头,Web 界面与 AI 流式才正常。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DBSHOOTER_DATA_DIR` | `./data` | 数据目录(内置 SQLite + secret.key) |
| `DBSHOOTER_SECRET` | 自动生成 | 加密主密钥(连接密码 / API Key) |
| `DBSHOOTER_TOKEN` | 空 | 设置后 `/api` 需 `Authorization: Bearer`,`/ws` 需 `?token=` |
| `DBSHOOTER_PORT` / `DBSHOOTER_HOST` | `5718` / `127.0.0.1` | 监听地址(Docker 镜像内为 `0.0.0.0`,由 Dockerfile 设置) |

## CI

`.github/workflows/ci.yml`:push / PR 时并行执行 **backend**(setup-python 3.12 → pyright + pytest + `pip check` + `pip install . && dbs --help` 打包冒烟)与 **frontend**(npm ci → vue-tsc 类型检查 → vitest → vite build),两者都绿后跑 **docker**(`docker build` + 镜像内 `dbs --help`)。

## 接口约定

只用 **GET / POST**:读 = GET,写/删/改 = POST action 路径(如 `POST /api/connections/delete`)。
实时通道:`/ws`(JSON 消息)承载查询执行推送与 AI 流式输出。详见 `docs/01-需求分析与技术选型.md`。

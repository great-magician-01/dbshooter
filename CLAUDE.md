# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DBShooter — Web 版多数据库管理工具(对标 DBeaver 核心体验)。Python 3.12 + FastAPI 后端 + Vue 3 + TypeScript 前端,单 Docker 镜像交付(前端构建产物由 FastAPI 静态托管)。**代码注释、docstring、提交信息均使用中文,新代码保持一致。**

## Commands

```bash
# 后端(仓库根目录,需先激活 .venv)
pip install -r requirements-dev.txt
python run.py                        # http://127.0.0.1:5718
DBSHOOTER_DEV=1 python run.py        # 开发热重载

# 前端(另开终端)
cd frontend
npm install
npm run dev                          # http://127.0.0.1:5173,已代理 /api 与 /ws 到 5718
npm run typecheck                    # vue-tsc --noEmit
npm run build                        # 产物 dist/(config.py 会自动探测并托管)

# 测试
pytest -q                                                  # 后端(pytest.ini 已设 testpaths/pythonpath)
pytest backend/tests/test_api.py -q                       # 单个文件
pytest backend/tests/test_api.py::test_name -q            # 单个用例
cd frontend && npm run test:run                           # 前端 vitest
cd frontend && npx vitest run tests/format.test.ts        # 单个文件

# 类型检查(与 VSCode Pylance 读同一份 pyrightconfig.json,须 0 error)
pyright                                                   # 装了 requirements-dev.txt 后可用;或 npx pyright
npx pyright backend/app/db.py                             # 单个文件

# Docker
docker build -t dbshooter .
```

CI(`.github/workflows/ci.yml`)在 push/PR 时跑 pyright + pytest + vitest + vite build。

## Backend Architecture(`backend/app/`)

分层:`drivers`(数据库抽象)→ `services`(运行时编排)→ `api`(REST + WS)。入口 `run.py` → `backend.app.main:app`(`create_app()` 工厂)。

- **drivers/** — 可插拔驱动注册表。`base.py` 定义 `DriverBase` / `MetaNode` / `ExecResult` / `QueryError`,驱动用 `@register` 注册、`create_driver(cfg)` 实例化。**新增数据库 = 新建一个 driver 文件 + 在 `drivers/__init__.py` import**。三种操作模型而非一种:`editor_mode` 区分 `sql`(sqlite/mysql/pg)/ `json-query`(mongo)/ `command`(redis),前端据此渲染不同 Tab 面板。SQL 类驱动实现 `ddl()` 供 text2sql 注入上下文。
- **services/connection_manager.py** — 单例 `manager`,按 conn_id 缓存已连接驱动(带锁防并发重复建连),配置从 `db.get_connection()` 读取。
- **services/query_service.py** — 查询会话:WS 触发异步执行 → 内存缓冲(`BUFFER_CAP=2000` 行,TTL 600s)→ 前端按 query_id 分页拉取。取消 = 驱动 `cancel()` + 杀任务 + `manager.evict`(断连)。
- **services/ai_service.py** — Text-to-SQL,仅依赖 OpenAI 兼容 Chat Completions 协议(流式)。生成 SQL 进编辑器由用户确认,不直接执行。
- **api/** — REST 路由 + WS。**约定:只用 GET/POST**,读 = GET,写/删/改 = POST action 路径(如 `POST /api/connections/delete`)。
- **api/ws.py** — 单 WS 端点 `/ws`,JSON 协议:客户端发 `{"id", "type", "payload"}`,服务端推 `{"id", "event", "data"}`,按 id 多路复用。消息类型 `query.execute` / `ai.text2sql`,handler 注册在 `HANDLERS`。
- **db.py** — 应用元数据存储(内置 SQLite):连接 / AI Provider / AI 会话 / 工作区页签 / 查询历史 / settings。**同步 sqlite3 + 全局 RLock**(元数据低频,刻意不用异步)。敏感字段(password / api_key)写入前经 `security.py` Fernet 加密。
- **config.py** — 环境变量覆盖:`DBSHOOTER_DATA_DIR`(默认 `./data`)、`DBSHOOTER_SECRET`(加密主密钥)、`DBSHOOTER_TOKEN`(设置后 REST 需 Bearer、WS 需 `?token=`)、`DBSHOOTER_PORT`/`DBSHOOTER_HOST`。
- **只读模式双保险**:AI prompt 约束 + `ensure_writable()` 语句首词拦截(`READONLY_PREFIXES`),在 driver 执行前抛 `ReadonlyViolation`。

## Python 类型检查标准(pyright basic,**0 error 才算完成**)

`pyrightconfig.json` 是唯一检查配置(venv 指向 `.venv`、basic 模式、include `backend` + `run.py`)。VSCode Pylance 与 CLI/CI 都读它 —— 调整检查行为只改这一个文件,不要在编辑器设置里另配。写后端代码必须遵守:

1. **`T | None` 必须收窄后再用**:取属性/下标/传参前先 `if x is None: ...` / `assert x is not None`。驱动连好资源后统一 `assert self.pool is not None` / `assert self.conn is not None`(`connect()` 之后的必然不变量)。
2. **签名禁用裸容器**:`dict` / `list` / `tuple` 单独出现在参数或返回值上会推导成 `Unknown`,必须写全:`dict[str, Any]`、`list[MetaNode]`、`tuple[Any, ...]`。行数据(库表记录)统一 `dict[str, Any]`。
3. **返回 `| None` 的查询交给调用方判空**:`db.one()` / `db.get_connection()` 等可能落空;写入后必然存在的按 id 回读用 `db._must()`。
4. **装饰器必须保留子类类型**:返回 cls 的装饰器用 TypeVar(`TypeVar('_T', bound=Base)`,见 `base.register`),写成 `-> type[Base]` 会抹平子类、令 isinstance 收窄失效。
5. **协程必须 await**:pyright 报"Result of async function call is not used"几乎必是真 bug(曾因此发现 SQLite 取消查询不生效)。
6. **三方库 stub 缺陷**才允许行级 `# pyright: ignore[规则名]`,并注明原因与移除条件(如 redis-py 8.1 的 hgetall/lrange/smembers async overload 失效)。禁止用 ignore 掩盖自己代码的类型问题。
7. **assert 仅用于不变量与类型收窄**(项目不以 `-O` 运行),业务校验用 `raise QueryError(...)`。
8. 副作用 import(如 drivers 注册)在 `__all__` 里登记,避免被判未使用。

## Frontend Architecture(`frontend/src/`)

Vue 3 + Pinia + CodeMirror 6。`@` alias 指向 `src/`。

- **api/http.ts** — axios 封装(统一错误处理 + 可选 token,localStorage `ds-token`)。
- **api/ws.ts** — 单 WS 连接多路复用客户端:按请求 id 路由事件,断线重连。
- **stores/** — `connections`(连接列表+树元数据)/ `workspace`(页签,自动持久化到后端 editor_tabs)/ `ai` / `theme`(暗亮双主题)/ `ui`。
- **components/panes/** — 按 `editor_mode` 三种工作台:`SqlPane` / `MongoPane` / `RedisPane`。图标统一用 `components/icons.ts`(`AppIcon` 组件),不用 emoji/Unicode 字形。
- **types.ts** — 前后端共享的接口类型定义(与 `backend/app/schemas.py` 对应)。

## Testing Notes

- `backend/tests/conftest.py` 在 import 任何 `backend.app` 模块**之前**设置 `DBSHOOTER_DATA_DIR`(临时目录)与 `DBSHOOTER_SECRET` —— config 模块在 import 时即读取环境变量并建目录,顺序不可颠倒。
- pytest-asyncio 为 `asyncio_mode = auto`,异步测试无需装饰器。
- 前端测试在 jsdom 环境跑(vite.config.ts 中 `test` 配置)。
- 后端测试不依赖真实 MySQL/PG/Redis/Mongo,用 SQLite 文件(`sqlite_db` fixture)覆盖 SQL 语义。

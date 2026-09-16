# DBShooter CLI 设计方案

> 目标:提供一个命令行客户端,直接调用现有 REST/WS 接口,覆盖脚本化查询、元数据查看、数据导出、AI text2sql 等场景。CLI 是**薄客户端**,不在本地复制后端逻辑。

## 1. 背景与目标

当前 DBShooter 的所有能力只能通过浏览器前端使用。引入 CLI 后:

- 脚本/CI 中可执行 SQL、导出 CSV(如定时任务、数据巡检);
- 终端用户无需打开浏览器即可查看连接、树状元数据、执行历史;
- AI text2sql 可在终端中流式使用;
- 对远程部署的实例(Docker / 其他机器)同样适用。

非目标:v1 不做交互式 REPL、不做 TUI、不在 CLI 端实现 SQL 补全。

## 2. 现状分析(可复用接口盘点)

### 2.1 REST(全部 GET/POST,薄客户端可直接映射)

| 能力 | 接口 | CLI 适配难度 |
|---|---|---|
| 健康检查/驱动列表 | `GET /api/health` | 直接 |
| 连接 CRUD/测试 | `/api/connections` + `/update` `/delete` `/test` | 直接 |
| 元数据树(懒加载) | `GET /api/connections/{cid}/metadata?path=` | 直接,按 path 逐层拉 |
| DDL | `GET /api/connections/{cid}/ddl?tables=` | 直接 |
| Redis 键详情 | `GET /api/connections/{cid}/key?key=&db=` | 直接 |
| 同步查询 | `POST /api/query/execute`(整包返回,`limit` 上限) | 直接,CLI 主通道 |
| 查询历史 | `GET /api/query/history` | 直接 |
| CSV 导出(流式) | `POST /api/query/export` | 直接,httpx 流式写盘 |
| AI Provider/会话/消息 | `/api/ai/...` | 直接 |
| settings | `/api/settings` + `/save` | 直接 |
| 工作区页签 | `/api/workspace/...` | v1 不暴露(纯 UI 状态) |

### 2.2 WS(`/ws`,JSON 多路复用)

- `query.execute`:进度推送 + 分页拉取。CLI 同步执行用 REST 已够,**v1 不消费此通道**;大结果集用 `export` 或调小 `limit`。
- `ai.text2sql`:唯一的 AI 调用通道(无 REST 版本)。CLI 的 `ai ask` 需要 WS 客户端。

### 2.3 鉴权

`DBSHOOTER_TOKEN` 设置后:REST 需 `Authorization: Bearer <token>`,WS 需 `?token=`。CLI 统一在客户端层注入,两个通道都要支持。

### 2.4 依赖现状

- `httpx>=0.27` 已是运行时依赖(ai_service 在用)——REST 客户端零新增。
- `uvicorn[standard]` 传递引入 `websockets`——可支撑 WS 客户端,但**不应依赖传递依赖**,需在 requirements 中显式声明。
- 无 `pyproject.toml`,入口为根目录 `run.py`。

## 3. 方案选型

### 方案 A:纯 HTTP/WS 远程客户端(推荐)

CLI 只通过 REST/WS 与服务端通信,本地不碰 `data/` 目录。

- ✅ 单一事实来源:只读拦截、历史记录、连接缓存等逻辑全部留在服务端,行为与 Web 完全一致;
- ✅ 天然支持远程实例与鉴权,也覆盖 Docker 部署;
- ✅ CLI 进程无状态,并发安全(SQLite 元数据只有服务端在写);
- ❌ 需要服务端先启动。

### 方案 B:本地直驱(绕过服务,直接 import drivers + db.py)

- ✅ 不需要启动服务;
- ❌ 与服务端并发读写同一 SQLite/连接资源,只读拦截、历史、加密都要在 CLI 重做一份,行为容易和 Web 漂移;
- ❌ 无法用于远程实例。

### 结论

采用方案 A。用户诉求"CLI 直接调用现在的接口"即指此。方案 B 的"免启动"诉求可用一条便利命令弥补:`dbs serve` 直接拉起本机服务(等价 `python run.py`),不作为 CLI 的内部模式。

## 4. 命令设计

命令风格:noun-verb 两级子命令(typer)。连接参数统一接受 **id、id 前缀、或名称**(唯一匹配,歧义时报错并列出候选)。

```
dbs serve [--host --port --dev]                  # 便利命令:拉起本机服务

dbs health                                       # 服务探活 + 驱动列表

dbs conn list                                    # 连接列表
dbs conn add --name N --type sqlite|mysql|pg|redis|mongo \
            [--host --port --database --username --password] \
            [--param k=v ...] [--readonly]       # 密码缺省时交互式隐式输入(getpass)
dbs conn update <conn> [...同上选项...]           # 只更新传入的字段
dbs conn delete <conn> [-y]
dbs conn test <conn>                             # 或 --config 式临时参数同 add

dbs tree <conn> [--path shop.users] [--depth 1]  # 元数据树;--depth>1 递归展开
dbs ddl <conn> <table...>                        # 打印 DDL
dbs key <conn> <key> [--db 0]                    # Redis 键详情

dbs query <conn> <stmt>                          # 执行(SQ驱动=SQL,mongo=JSON查询,redis=命令)
dbs query <conn> -f script.sql                   # 从文件读语句
dbs query <conn> --stdin                         # 从管道读:cat a.sql | dbs query prod --stdin
       [--limit 500] [--schema public]           # schema 仅 PG 生效(与后端语义一致)
       [--format table|json|csv|raw]             # 默认:TTY=table,管道=csv(脚本友好)
       [-o out.csv]                              # 等价 --format csv -o
dbs export <conn> <stmt> -o out.csv [--limit 10000]  # 大结果集走 /api/query/export 流式落盘
dbs history [--limit 50] [--format ...]

dbs ai ask <conn> <question> [--tables a,b] [--session <id>]   # WS 流式输出,结束时打印提取的 SQL
dbs ai providers list / add / activate <id> / test
dbs ai sessions list                             # v1 只读浏览

dbs settings get [key] / set <key> <value>       # 通用设置(主题等对 CLI 无意义的键原样透传)
```

全局选项(每个命令可用):

```
-s, --server URL     默认 http://127.0.0.1:5718;env: DBSHOOTER_URL
-t, --token TOKEN    env: DBSHOOTER_TOKEN(与服务端同一变量名)
    --timeout SEC    默认连接 5s / 读 60s;ai ask 读 300s
    --format ...     全局默认输出格式
    --no-color       关闭颜色与富表格(同时尊重 NO_COLOR 环境变量)
-v, --verbose        打印请求/响应摘要,便于排查
```

退出码:`0` 成功;`1` 业务失败(查询错误、测试不通过等,服务端 message 原样输出);`2` 用法错误(typer 自带);`3` 无法连接服务(提示 `dbs serve` 或检查 `--server`);`4` 未授权(401/WS 4401)。

## 5. 技术设计

### 5.1 代码结构

新增 `backend/cli/` 包,复用后端进程内零逻辑、纯客户端:

```
backend/cli/
  __init__.py
  main.py          # typer 应用装配,子命令注册
  client.py        # httpx.Client 封装:base_url/鉴权头/超时/错误归一化;WS 封装(websockets)
  resolve.py       # 连接名/id 前缀 → conn_id 解析
  output.py        # 渲染:table(rich)/ json / csv / raw;TTY 与 --no-color 判定
  commands/
    conn.py  tree.py  query.py  history.py  ai.py  settings.py  misc.py
```

入口两种方式同时可用:

1. `python -m backend.cli ...`(零安装,仓库内即用,CI/开发场景);
2. 新增最小 `pyproject.toml`(hatchling),注册 console script `dbs = backend.cli.main:app`,`pip install .` 后获得 `dbs` 命令;Docker 镜像内同样安装,支持 `docker exec <c> dbs ...`。

### 5.2 新增依赖

- `typer`(命令行框架,自带补全/帮助);
- `rich`(表格与流式渲染,typer 官方搭档);
- `websockets`(显式声明,供 `ai ask`)。

均加入 `requirements.txt`;`pyproject.toml` 的依赖与 requirements 保持一致(以 requirements 为准生成或直接列写)。

### 5.3 客户端层要点(client.py)

- REST:`httpx.Client(base_url=...)`,统一注入 `Authorization: Bearer`;401 → 退出码 4;连接异常 → 退出码 3 并给中文提示;业务错误沿用 FastAPI 的 `{"detail": ...}` 提取 message。
- `export`:用 `client.stream('POST', ...)` 分块写文件,避免大结果集进内存;文件名取 `-o` 或响应头 `Content-Disposition`。
- WS(`ai ask`):`websockets.connect(f"{ws_url}/ws?token=...")`(http→ws  scheme 转换),发送 `{"id", "type": "ai.text2sql", "payload"}`,按 `event` 渲染:`ai.token` 流式打印、`ai.done` 落 SQL 提示、`ai.error` 非零退出。多路复用按 id 过滤无关消息。
- 所有中文注释,风格与后端一致。

### 5.4 输出渲染(output.py)

- `table`:rich Table,列来自 `ExecResult.columns`;`affected`/`command`/`documents` 三种 kind 分别有渲染分支(mongo documents 逐行 JSON 美化;redis command 打印 raw);
- `json`:整包 `ExecResult.to_dict()` 输出(结构稳定,供 jq);
- `csv`:与 `/api/query/export` 相同编码约定(utf-8-sig);
- `raw`:仅值、tab 分隔,便于 `cut/awk`;
- 多结果集(一次执行多条语句)逐个渲染,JSON 模式输出数组。

### 5.5 配置解析优先级

命令行 flag > 环境变量(`DBSHOOTER_URL` / `DBSHOOTER_TOKEN`)> 默认值。
v1 不做配置文件;若后续需要多环境,再加 `~/.dbshooter/config.toml` 的 profile 机制(文档预留,不在本期)。

### 5.6 只读与安全

- 只读拦截完全由服务端 `ensure_writable()` 兜底,CLI 不做、也不应做第二份判断(避免漂移);
- token 不落盘、不进 shell 历史:推荐用 env 注入;`-t` 仅作应急;
- `conn add` 的 `--password` 缺省时走 getpass 隐式输入。

## 6. 测试方案

- `backend/tests/test_cli.py`:用 `httpx.ASGITransport` 把 CLI 的 client 指到内存中的 FastAPI app(复用现有 conftest 的临时数据目录),走真实路由断言输出与退出码 —— 不需要起真实端口;
- WS 部分用 `fastapi.testclient` 的 websocket 或单独起 uvicorn 子进程做一到两个冒烟用例;
- 覆盖:连接解析(名/id/前缀/歧义)、query 四种 format、export 写盘、401/连不上的退出码、ai ask 事件流(mock provider 或直接断言错误路径);
- CI 现有 job 里加跑同一 pytest 即可,无新流水线。

## 7. 里程碑

| 阶段 | 内容 | 验收 |
|---|---|---|
| M1 | pyproject + client/output/resolve 骨架 + `health`/`conn` 全组 + `tree`/`ddl`/`key` | pytest 通过,`dbs conn list` 可用 |
| M2 | `query`(四种格式/stdin/-f)+ `export` + `history` | 管道场景闭环:`dbs query ... --format csv | ...` |
| M3 | `ai ask`(WS 流式)+ providers/sessions 只读命令 | 终端流式输出并落会话消息 |
| M4 | `serve`、补全安装提示、README/文档、Docker 镜像内验证 | `docker exec` 可执行 dbs |

## 8. 边界与风险

- **服务端未启动**:所有命令在连不上时给同一提示(退出码 3),文案指向 `dbs serve` / `--server`;
- **大结果集**:同步 `execute` 有 `limit` 上限,超限 `truncated=true` 时 CLI 明确提示"已截断,用 dbs export 拉全量";
- **WS 是 AI 唯一通道**:若后续 Web 也需要非流式 AI,可再评估加 REST 版;本期不为此改后端;
- **版本兼容**:CLI 与服务端同仓库同发布,不做跨版本协议协商;`health` 返回的驱动列表可用于 CLI 侧能力提示;
- **Windows 终端**:rich 对 GBK/旧终端自动降级,`--no-color` 兜底;路径参数统一 UTF-8。

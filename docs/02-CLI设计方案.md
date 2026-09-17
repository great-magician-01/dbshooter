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

命令风格:noun-verb 两级子命令(typer)。连接参数统一接受 **id、id 前缀、名称、名称前缀**(优先级:精确 id > 精确名称 > 唯一 id 前缀 > 唯一名称前缀;歧义时报错并列出候选)。

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

dbs ai ask <conn> <question> [--session <id>] [--sql]   # WS 流式;缺省自动建会话(标题取问题前缀),
                                                          # --session 复用;--sql 只输出提取的 SQL,可管道给 query --stdin
                                                          # 注:表结构由服务端 AI 自助查表工具获取,无 --tables 参数
dbs ai providers list / add / activate <id> / test <id>
dbs ai sessions list                             # v1 只读浏览

dbs settings get [key] / set <key> <value>       # 通用设置(主题等对 CLI 无意义的键原样透传)
```

全局选项(**须写在子命令之前**,Click 组级选项的固有限制):

```
-s, --server URL     默认 http://127.0.0.1:5718;env: DBSHOOTER_URL
-t, --token TOKEN    env: DBSHOOTER_TOKEN(与服务端同一变量名)
    --timeout SEC    默认连接 5s / 读 60s;ai ask 读 300s
    --no-color       关闭颜色与富表格(同时尊重 NO_COLOR 环境变量)
```

`--format table|json|csv|raw` 挂在各输出型子命令上(`dbs conn list --format json` 这种自然语序);
默认 TTY=table、管道=csv。

退出码:`0` 成功;`1` 业务失败(查询错误、测试不通过等,服务端 message 原样输出);`2` 用法错误(typer 自带);`3` 无法连接服务(提示 `dbs serve` 或检查 `--server`);`4` 未授权(401/WS 4401)。

## 5. 技术设计

### 5.1 代码结构

新增 `backend/cli/` 包,复用后端进程内零逻辑、纯客户端:

```
backend/cli/
  __init__.py
  __main__.py      # python -m backend.cli
  main.py          # typer 应用装配,全局选项 callback
  errors.py        # CliError + 退出码 + handle_cli_error 命令装饰器
  client.py        # ApiClient(httpx 封装:鉴权/超时/错误归一化;inner 可替换)
  wsclient.py      # WS 同步客户端(websockets.sync),ai.text2sql 事件流
  state.py         # 全局选项状态(ctx.obj)
  resolve.py       # 连接引用解析:精确 id > 精确名称 > 唯一 id 前缀 > 唯一名称前缀
  output.py        # 渲染:table(rich)/ json / csv / raw;TTY 与 NO_COLOR 判定;CSV 公式注入防护
  commands/
    conn.py  meta.py(tree/ddl/key)  query.py(query/export/history)
    ai.py    misc.py(health/serve/settings)
```

入口两种方式同时可用:

1. `python -m backend.cli ...`(零安装,仓库内即用,CI/开发场景);
2. 新增最小 `pyproject.toml`(hatchling),注册 console script `dbs = backend.cli.main:app`,`pip install .` 后获得 `dbs` 命令;Docker 镜像内同样安装,支持 `docker exec <c> dbs ...`。

### 5.2 新增依赖

- `typer`(命令行框架,自带补全/帮助);
- `rich`(表格与流式渲染,typer 官方搭档);
- `websockets>=15`(显式声明,供 `ai ask`;`proxy=None` 关闭环境代理是 15.0 才有的参数,下限不能更低);
- `pymongo>=4`(mongo 驱动直接 `import bson`,该模块实际由 pymongo 提供,不能只靠 motor 的传递依赖)。

均加入 `requirements.txt`;`pyproject.toml` 的依赖与 requirements 保持一致(以 requirements 为准生成或直接列写)。

### 5.3 客户端层要点(client.py)

- REST:`httpx.Client(base_url=...)`,统一注入 `Authorization: Bearer`;401 → 退出码 4;连接异常 → 退出码 3 并给中文提示;业务错误沿用 FastAPI 的 `{"detail": ...}` 提取 message。
- `export`:用 `client.stream('POST', ...)` 分块写文件,避免大结果集进内存;文件名取 `-o` 或响应头 `Content-Disposition`。
- WS(`ai ask`):`websockets.connect(f"{ws_url}/ws?token=...")`(http→ws  scheme 转换),发送 `{"id", "type": "ai.text2sql", "payload"}`,按 `event` 渲染:`ai.token` 流式打印、`ai.done` 落 SQL 提示、`ai.error` 非零退出。多路复用按 id 过滤无关消息。
- 所有中文注释,风格与后端一致。

### 5.4 输出渲染(output.py)

- `table`:rich Table,列来自 `ExecResult.columns`;`affected`/`command`/`documents` 三种 kind 分别有渲染分支(mongo documents 逐行 JSON 美化;redis command 打印 raw);
- `json`:整包 `ExecResult.to_dict()` 输出(结构稳定,供 jq);
- `csv`:与 `/api/query/export` 相同编码约定(utf-8-sig);字符串单元格以公式引导字符(`= + - @ \t \r`)开头时加 `'` 前缀(公式注入防护,前缀集合与服务端 `_csv_safe` 保持同步;数值不受影响);`-o` 在纯写语句(无结果集)时写出 0 字节空文件并提示,避免脚本 `&& cat` 读到陈旧内容;
- `raw`:仅值、tab 分隔,便于 `cut/awk`(管道语义,不做公式中和);
- 多结果集(一次执行多条语句)逐个渲染,JSON 模式输出数组。

### 5.5 配置解析优先级

命令行 flag > 环境变量(`DBSHOOTER_URL` / `DBSHOOTER_TOKEN`)> 默认值。
v1 不做配置文件;若后续需要多环境,再加 `~/.dbshooter/config.toml` 的 profile 机制(文档预留,不在本期)。

### 5.6 只读与安全

- 只读拦截完全由服务端 `ensure_writable()` 兜底,CLI 不做、也不应做第二份判断(避免漂移);
- token 不落盘、不进 shell 历史:推荐用 env 注入;`-t` 仅作应急;
- token 不进错误信息/日志:REST 与 WS 的报错统一走 `errors.mask_url`(剥 `?查询串` 与 `user:pass@`),异常文本里自带的 uri 再经 `_scrub` 兜底;
- `conn add` 的 `--password` 缺省时走 getpass 隐式输入;
- `dbs serve` 默认只监听 `127.0.0.1`;监听非本机地址且未设 `DBSHOOTER_TOKEN` 时启动即警告。

## 6. 测试方案

- `backend/tests/test_cli.py`:**FastAPI TestClient 直插 `ApiClient`**(`ApiClient(inner)` 的 inner 就是 httpx.Client,TestClient 是其子类),monkeypatch `cli.state.make_client` 后所有命令走真实路由断言输出与退出码 —— 不需要起真实端口;
- WS 部分:`stream_ai_events` 作为函数级替换点打桩,断言事件流渲染逻辑;服务端 WS 行为由 test_ws.py 覆盖;
- 覆盖:连接解析(精确 id/精确名称/前缀/歧义)、query 四种 format、export 写盘、stdin/-f(UTF-8 与 GBK)、只读拦截透出、ai ask 事件流、settings 读写,以及历次审查回归(CSV 公式注入、WS 报错脱敏、地址非法无 traceback、`serve` 默认监听等);
- CI 的 backend job 里跑同一 pytest(外加 pyright、`pip check`、`pip install . && dbs --help` 打包冒烟),另有 docker job 做镜像构建冒烟。

注意:元数据 SQLite 在测试会话内共享,用例里连接名必须带随机后缀,避免跨用例撞名。

## 7. 里程碑(已按此实施)

| 阶段 | 内容 | 状态 |
|---|---|---|
| M1 | pyproject + client/output/resolve 骨架 + `health`/`conn` 全组 + `tree`/`ddl`/`key` | ✅ |
| M2 | `query`(四种格式/stdin/-f)+ `export` + `history` | ✅ |
| M3 | `ai ask`(WS 流式 + `--sql` 管道)+ providers/sessions 命令 | ✅ |
| M4 | `serve`、README、方案文档同步 | ✅ |

## 8. 边界与风险

- **服务端未启动**:所有命令在连不上时给同一提示(退出码 3),文案指向 `dbs serve` / `--server`;
- **大结果集**:同步 `execute` 有 `limit` 上限,超限 `truncated=true` 时 CLI 明确提示"已截断,用 dbs export 拉全量";
- **WS 是 AI 唯一通道**:若后续 Web 也需要非流式 AI,可再评估加 REST 版;本期不为此改后端;
- **版本兼容**:CLI 与服务端同仓库同发布,不做跨版本协议协商;`health` 返回的驱动列表可用于 CLI 侧能力提示;
- **Windows 终端**:rich 对 GBK/旧终端自动降级,`--no-color` 兜底;路径参数统一 UTF-8。

"""AI:text2sql 问答(WS 流式)+ Provider/会话管理。"""
from __future__ import annotations

import sys
from typing import Any

import typer

from ..client import ApiClient, resolve_base, resolve_token
from ..errors import CliError, handle_cli_error
from ..output import make_console, print_json, print_rows, resolve_list_format, warn
from ..resolve import resolve_conn
from ..state import get_state
from ..wsclient import stream_ai_events, ws_url

app = typer.Typer(help='AI text2sql 与 Provider 管理', no_args_is_help=True)
providers_app = typer.Typer(help='AI Provider 管理', no_args_is_help=True)
sessions_app = typer.Typer(help='AI 会话', no_args_is_help=True)
app.add_typer(providers_app, name='providers')
app.add_typer(sessions_app, name='sessions')

_FMT_OPT = typer.Option(None, '--format', help='输出格式: table/json')


@app.command()
@handle_cli_error
def ask(ctx: typer.Context,
        conn: str = typer.Argument(..., metavar='CONN', help='连接 id / id 前缀 / 名称'),
        question: str = typer.Argument(..., help='自然语言问题'),
        session: str | None = typer.Option(None, '--session', help='复用已有会话 id(缺省新建)'),
        sql_only: bool = typer.Option(False, '--sql', help='只输出提取的 SQL(可管道给 dbs query --stdin)')) -> None:
    """AI 问答:流式输出回答,结束时提示提取的 SQL。SQL 类连接会自动调用查表工具。"""
    st = get_state(ctx)
    client = st.client()
    row = resolve_conn(client, conn)
    session_id = session
    created_id: str | None = None   # 本次命令新建的会话,WS 失败时要补偿删除(不留孤儿)
    if not session_id:
        item = client.post('/api/ai/sessions',
                           {'title': question[:30], 'connection_id': row['id']})['item']
        session_id = item['id']
        created_id = session_id
    url = ws_url(resolve_base(st.server), resolve_token(st.token))
    payload = {'session_id': session_id, 'conn_id': row['id'], 'question': question}

    done: dict[str, Any] | None = None
    got_event = False
    try:
        for event, data in stream_ai_events(url, payload, max(st.timeout, 300.0)):
            got_event = True
            if event == 'ai.token':
                if not sql_only:
                    print(data.get('delta', ''), end='', flush=True)
            elif event == 'ai.tool':
                # 工具轨迹走 stderr,不污染 stdout 的正文/管道
                warn(f"[工具:{data.get('status')}] {data.get('name')} "
                     f"{_short_args(data.get('args'))} {data.get('summary', '')}")
            elif event in ('ai.error', 'error'):
                raise CliError(str(data.get('message') or 'AI 调用失败'))
            elif event == 'ai.done':
                done = data
    except CliError:
        # 一个事件都没收到 = WS 压根没建起来:刚建的会话没人用过,删掉;
        # 已经跑起来的(AI 报错等)保留,里面有对话痕迹
        if created_id is not None and not got_event:
            _delete_session_quiet(client, created_id)
        raise
    sql = (done or {}).get('sql') or ''
    if sql_only:
        if not sql:
            raise CliError('AI 未产出 SQL')
        print(sql)
        return
    print()   # 流式正文收尾换行
    if sql:
        typer.echo('—— 提取的 SQL ——')
        typer.echo(sql)
    if done:
        warn(f"(会话 {session_id},耗时 {done.get('elapsed_ms', 0)}ms)")


def _short_args(args: Any, width: int = 80) -> str:
    s = str(args or '')
    return s if len(s) <= width else s[:width - 1] + '…'


def _delete_session_quiet(client: ApiClient, sid: str) -> None:
    """补偿删除:清理失败只当没做成,不能盖掉用户已经看到的原始错误。"""
    try:
        client.post('/api/ai/sessions/delete', {'id': sid})
    except Exception:   # noqa: BLE001 - 尽力而为的清理,任何异常都不该影响退出码与报错
        pass


# ── providers ──

@providers_app.command('list')
@handle_cli_error
def providers_list(ctx: typer.Context, fmt: str | None = _FMT_OPT) -> None:
    """列出 AI Provider(密钥不下发,只显示是否已设置)。"""
    st = get_state(ctx)
    items: list[dict[str, Any]] = st.client().get('/api/ai/providers')['items']
    if resolve_list_format(fmt) == 'json':
        print_json(items)
        return
    print_rows(['ID', '名称', 'base_url', '模型', '生效'],
               [[p['id'][:8], p['name'], p['base_url'], p['model'],
                 '✓' if p.get('is_active') else ''] for p in items],
               make_console(st.no_color))


@providers_app.command('add')
@handle_cli_error
def providers_add(ctx: typer.Context,
                  name: str = typer.Option(..., '--name'),
                  base_url: str = typer.Option(..., '--base-url', help='OpenAI 兼容地址,如 https://api.deepseek.com/v1'),
                  model: str = typer.Option(..., '--model'),
                  api_key: str | None = typer.Option(None, '--api-key', help='缺省时交互式隐式输入'),
                  activate: bool = typer.Option(False, '--activate', help='同时设为生效 Provider')) -> None:
    """新增 Provider(OpenAI 兼容 Chat Completions)。"""
    if api_key is None:
        # 非交互(CI / cron / </dev/null)不能弹提示,否则 typer 直接 Abort;
        # 与 conn._ask_password 同口径:留空跳过(本地推理服务允许无 key)
        if not sys.stdin.isatty():
            warn('非交互环境:跳过 API Key 输入(需要时用 --api-key 提供)')
            api_key = ''
        else:
            api_key = typer.prompt('API Key(留空跳过)', hide_input=True, default='', show_default=False)
    client = get_state(ctx).client()
    item = client.post('/api/ai/providers', {'name': name, 'base_url': base_url,
                                             'api_key': api_key, 'model': model})['item']
    if activate:
        client.post('/api/ai/providers/activate', {'id': item['id']})
    typer.echo(f"已创建 Provider {item['name']} (id {item['id'][:8]})" + (',已生效' if activate else ''))


@providers_app.command('activate')
@handle_cli_error
def providers_activate(ctx: typer.Context,
                       pid: str = typer.Argument(..., help='Provider id(完整或前缀)')) -> None:
    """设为生效 Provider。"""
    client = get_state(ctx).client()
    item = _resolve_provider(client, pid)
    client.post('/api/ai/providers/activate', {'id': item['id']})
    typer.echo(f"已生效: {item['name']}")


@providers_app.command('test')
@handle_cli_error
def providers_test(ctx: typer.Context,
                   pid: str = typer.Argument(..., help='Provider id(完整或前缀)')) -> None:
    """连通性测试(用库里已保存的配置)。"""
    client = get_state(ctx).client()
    item = _resolve_provider(client, pid)
    r = client.post('/api/ai/providers/test',
                    {'id': item['id'], 'name': item['name'], 'base_url': item['base_url'],
                     'model': item['model']})
    if r['ok']:
        typer.echo(f"连接成功: {r['message']}")
        return
    raise CliError(f"连接失败: {r['message']}")


def _resolve_provider(client: Any, ref: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = client.get('/api/ai/providers')['items']
    hit = [p for p in items if p['id'] == ref] or [p for p in items if str(p['id']).startswith(ref)]
    if len(hit) == 1:
        return hit[0]
    if hit:
        raise CliError(f'Provider "{ref}" 匹配到多个,请用更长的 id 前缀')
    raise CliError(f'找不到 Provider: {ref}(用 dbs ai providers list 查看)')


# ── sessions ──

@sessions_app.command('list')
@handle_cli_error
def sessions_list(ctx: typer.Context, fmt: str | None = _FMT_OPT) -> None:
    """列出 AI 会话。"""
    st = get_state(ctx)
    items: list[dict[str, Any]] = st.client().get('/api/ai/sessions')['items']
    if resolve_list_format(fmt) == 'json':
        print_json(items)
        return
    print_rows(['ID', '标题', '更新时间'],
               [[s['id'][:8], s['title'], str(s.get('updated_at', '')).replace('T', ' ')]
                for s in items],
               make_console(st.no_color))

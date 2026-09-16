"""输出渲染:table(rich)/ json / csv / raw。

数据内容一律不经 console.print 打印(避免数据中 "[" 被当 rich markup),
只有纯提示文案才用 console.print 的样式。
"""
from __future__ import annotations

import csv
import io
import json
import sys
from typing import Any

from rich.console import Console
from rich.table import Table

FMT_CHOICES = ('table', 'json', 'csv', 'raw')


def make_console(no_color: bool = False) -> Console:
    """表格/提示输出。管道场景自动去色,ANSI 不污染重定向。"""
    return Console(no_color=no_color or not sys.stdout.isatty(), highlight=False)


def default_format() -> str:
    """未显式指定时:TTY 用表格,管道用 csv(脚本友好)。"""
    return 'table' if sys.stdout.isatty() else 'csv'


def resolve_format(fmt: str | None) -> str:
    """校验 --format;未指定时按 TTY/管道自动选择。

    注:--format 挂在各子命令上而非根命令——Click 要求组级选项必须写在子命令之前,
    `dbs conn list --format json` 这种自然语序只有子命令级选项才支持。
    """
    if fmt is None:
        return default_format()
    if fmt not in FMT_CHOICES:
        from .errors import CliError  # 延迟 import:errors 依赖 typer,避免环
        raise CliError(f'不支持的格式: {fmt}(可选 {"/".join(FMT_CHOICES)})')
    return fmt


def warn(msg: str) -> None:
    """提示信息统一走 stderr,不污染 stdout 的数据流。"""
    print(msg, file=sys.stderr)


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _cell(v: Any, null: str = '') -> str:
    if v is None:
        return null
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return str(v)


def print_rows(headers: list[str], rows: list[list[Any]], console: Console) -> None:
    """通用列表渲染(连接列表、历史等非查询结果)。"""
    t = Table()
    for h in headers:
        t.add_column(h)
    for row in rows:
        t.add_row(*[_cell(v) for v in row])
    console.print(t)


def result_to_csv(result: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c['name'] for c in result.get('columns', [])])
    for row in result.get('rows', []):
        w.writerow([_cell(v) for v in row])
    return buf.getvalue()


def print_results(results: list[dict[str, Any]], fmt: str, console: Console) -> list[str]:
    """渲染 ExecResult 列表,返回错误信息列表(非空 → 调用方按业务失败退出)。"""
    errors = [r['error'] for r in results if r.get('error')]
    if fmt == 'json':
        print(json.dumps(results, ensure_ascii=False, default=str))
        return errors
    if fmt in ('csv', 'raw'):
        first = next((r for r in results
                      if r.get('kind') in ('rows', 'documents') and not r.get('error')), None)
        if first is not None:
            if fmt == 'csv':
                sys.stdout.write(result_to_csv(first))
            else:
                for row in first.get('rows', []):
                    print('\t'.join(_cell(v) for v in row))
        _print_affected(results)
        _hint_truncated(results)
        return errors
    for r in results:
        _print_table(r, console)
    _hint_truncated(results)
    return errors


def _print_affected(results: list[dict[str, Any]]) -> None:
    for r in results:
        if r.get('kind') == 'affected' and r.get('affected') is not None and not r.get('error'):
            warn(f"影响行数: {r['affected']}")


def _hint_truncated(results: list[dict[str, Any]]) -> None:
    if any(r.get('truncated') for r in results):
        warn('结果已截断(达到 limit),拉全量请用 --limit 调大,或使用 dbs export')


def _suffix(r: dict[str, Any]) -> str:
    elapsed = r.get('elapsed_ms')
    return f' ({elapsed}ms)' if elapsed is not None else ''


def _print_table(r: dict[str, Any], console: Console) -> None:
    kind = r.get('kind', 'rows')
    if r.get('error'):
        console.print(f"错误: {r['error']}{_suffix(r)}", style='red')
        return
    if kind == 'affected':
        console.print(f"影响行数: {r.get('affected', 0)}{_suffix(r)}")
        return
    if kind == 'command':
        # redis 等命令回包:原样打印,不走表格
        print(_cell(r.get('raw'), null='(空)'))
        return
    cols = r.get('columns', [])
    t = Table()
    for c in cols:
        t.add_column(str(c['name']))
    for row in r.get('rows', []):
        t.add_row(*[_cell(v, null='NULL') for v in row])
    console.print(t)
    console.print(f"[dim]共 {len(r.get('rows', []))} 行{_suffix(r)}[/]")

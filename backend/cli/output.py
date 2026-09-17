"""输出渲染:table(rich)/ json / csv / raw。

数据内容一律不经 console.print 的 markup 解析(避免数据中 "[" 被当 rich markup):
凡进 rich 的单元格都用 rich.text.Text 包裹,控制字符(ESC 等)转义后再显示。
"""
from __future__ import annotations

import csv
import json
import re
import sys
from typing import Any, TextIO

from rich.console import Console
from rich.table import Table
from rich.text import Text

FMT_CHOICES = ('table', 'json', 'csv', 'raw')

# 含数据可渲染的结果集 kind(affected=仅行数,error=错误)
_DATA_KINDS = ('rows', 'documents', 'command')

# 终端控制字符(保留 \t \n):不可信库数据里的 ESC 序列能改色/建超链接/清屏
_CTRL_RE = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')


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


def resolve_list_format(fmt: str | None) -> str:
    """列表型命令(conn list/history/sessions 等)只支持 table/json;csv/raw 无意义。"""
    if fmt is None:
        return 'table'
    if fmt not in ('table', 'json'):
        from .errors import CliError
        raise CliError(f'该命令只支持 --format table|json: {fmt}')
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


def _plain(v: Any, null: str = '') -> str:
    """单元格纯文本:控制字符转义成可见的 \\xNN(不剥原始数据,但终端状态不可被改)。"""
    return _CTRL_RE.sub(lambda m: f'\\x{ord(m.group()):02x}', _cell(v, null))


def _text(v: Any, null: str = '') -> Text:
    """rich 单元格:用 Text 包住数据,否则 "[b]x[/b]"/"[link=…]" 会被当 markup 吞字或建超链接。"""
    return Text(_plain(v, null))


def print_rows(headers: list[str], rows: list[list[Any]], console: Console) -> None:
    """通用列表渲染(连接列表、历史等非查询结果)。表头是 CLI 自带文案,数据走 _text。"""
    t = Table()
    for h in headers:
        t.add_column(h)
    for row in rows:
        t.add_row(*[_text(v) for v in row])
    console.print(t)


def data_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """含数据的结果集(按执行顺序)。多语句/多结果集必须逐个渲染,不能只取第一条。"""
    out: list[dict[str, Any]] = []
    for r in results:
        if r.get('error') or r.get('kind') not in _DATA_KINDS:
            continue
        # command(redis 等)带 columns/rows;mongo aggregate 只有 raw
        if r.get('columns') or r.get('raw') not in (None, '', [], {}):
            out.append(r)
    return out


def _jsonl(result: dict[str, Any]) -> list[str]:
    """无列结构的结果集(mongo aggregate 等):raw 里每条文档序列化成一行 JSON。"""
    raw = result.get('raw')
    items = raw if isinstance(raw, list) else [raw]
    return [json.dumps(d, ensure_ascii=False, default=str) for d in items]


def write_results_csv(results: list[dict[str, Any]], out: TextIO) -> int:
    """全部含数据的结果集逐个写 CSV(各自带头行;无列结构的按 JSONL),返回数据行数。"""
    n = 0
    w = csv.writer(out)
    for r in data_results(results):
        if r.get('columns'):
            w.writerow([c['name'] for c in r['columns']])
            for row in r.get('rows', []):
                w.writerow([_cell(v) for v in row])
                n += 1
        else:
            for line in _jsonl(r):
                out.write(line + '\n')
                n += 1
    return n


def write_results_raw(results: list[dict[str, Any]], out: TextIO) -> int:
    """raw:每行一条记录、tab 分隔;无列结构的结果集退化为 JSONL(与 csv 一致,不静默丢)。"""
    n = 0
    for r in data_results(results):
        if r.get('columns'):
            for row in r.get('rows', []):
                out.write('\t'.join(_cell(v) for v in row) + '\n')
                n += 1
        else:
            for line in _jsonl(r):
                out.write(line + '\n')
                n += 1
    return n


def print_results(results: list[dict[str, Any]], fmt: str, console: Console | None) -> list[str]:
    """渲染 ExecResult 列表,返回错误信息列表(非空 → 调用方按业务失败退出)。

    console 仅 table 分支使用,csv/json/raw 可传 None。"""
    errors = [r['error'] for r in results if r.get('error')]
    if fmt == 'json':
        print(json.dumps(results, ensure_ascii=False, default=str))
        return errors
    if fmt in ('csv', 'raw'):
        if fmt == 'csv':
            write_results_csv(results, sys.stdout)
        else:
            write_results_raw(results, sys.stdout)
        _print_affected(results)
        _hint_truncated(results)
        return errors
    for r in results:
        assert console is not None  # table 分支调用方必传
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
        console.print(Text(f"错误: {_plain(r['error'])}{_suffix(r)}"), style='red')
        return
    if kind == 'affected':
        console.print(f"影响行数: {r.get('affected', 0)}{_suffix(r)}")
        return
    if kind == 'command':
        # redis 等命令回包:原样打印,不走表格也不走 rich(raw 里的换行/制表符要保真)
        print(_plain(r.get('raw'), null='(空)'))
        return
    cols = r.get('columns', [])
    if kind == 'documents' and not cols:
        # mongo aggregate 等无列结构的结果:逐条 JSON(见 docs/02-CLI设计方案.md 5.4)
        docs = _jsonl(r)
        for line in docs:
            console.print_json(line)
        console.print(Text(f"共 {len(docs)} 条{_suffix(r)}", style='dim'))
        return
    t = Table()
    for c in cols:
        t.add_column(Text(str(c['name'])))   # 列名来自库,同样按数据处理
    for row in r.get('rows', []):
        t.add_row(*[_text(v, null='NULL') for v in row])
    console.print(t)
    console.print(Text(f"共 {len(r.get('rows', []))} 行{_suffix(r)}", style='dim'))

"""连接引用解析:id 精确 → id 前缀 → 名称,歧义时报错并列候选。"""
from __future__ import annotations

from typing import Any

from .client import ApiClient
from .errors import CliError


def resolve_conn(client: ApiClient, ref: str) -> dict[str, Any]:
    """把用户输入的连接引用(id / id 前缀 / 名称)解析为连接记录。"""
    if not ref.strip():
        # 空 ref 会让 startswith('') 恒真:单连接时静默落到唯一连接上(危险)
        raise CliError('请提供连接 id / 前缀 / 名称')
    items: list[dict[str, Any]] = client.get('/api/connections')['items']
    exact_id = [c for c in items if c['id'] == ref]
    if exact_id:
        return exact_id[0]
    prefix = [c for c in items if str(c['id']).startswith(ref)]
    if len(prefix) == 1:
        return prefix[0]
    by_name = [c for c in items if c['name'] == ref]
    if len(by_name) == 1:
        return by_name[0]
    candidates = prefix if len(prefix) > 1 else by_name
    if len(candidates) > 1:
        lines = '\n'.join(f"  {c['id'][:8]}  {c['name']} ({c['type']})" for c in candidates)
        raise CliError(f'连接 "{ref}" 匹配到多个,请用更长的 id 前缀:\n{lines}')
    raise CliError(f'找不到连接: {ref}(用 dbs conn list 查看现有连接)')

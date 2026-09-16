"""杂项:health(服务探活)。"""
from __future__ import annotations

import typer

from ..errors import handle_cli_error
from ..state import get_state


@handle_cli_error
def health(ctx: typer.Context) -> None:
    """服务探活 + 已注册驱动列表。"""
    r = get_state(ctx).client().get('/api/health')
    typer.echo('服务正常,驱动: ' + ', '.join(r['drivers']))

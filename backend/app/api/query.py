"""查询:同步执行(备用通道)/ 分页 / 取消 / 历史 / CSV 导出。主通道是 /ws 的 query.execute。"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .. import db
from ..drivers import QueryError
from ..schemas import CancelIn, ExecuteIn, ExportIn
from ..services.connection_manager import manager
from ..services.query_service import query_service

router = APIRouter(prefix='/api/query', tags=['query'])


@router.post('/execute')
async def execute(body: ExecuteIn):
    """同步执行(整包返回,适合小查询;大结果集/进度推送走 WS)。"""
    try:
        driver = await manager.get(body.conn_id)
    except QueryError as e:
        raise HTTPException(404, str(e))
    try:
        results = await driver.execute(body.stmt, limit=body.limit)
    except QueryError as e:
        db.add_history(body.conn_id, body.stmt, 0, 0, 'blocked')
        raise HTTPException(400, str(e))
    first = next((r for r in results if not r.error), None)
    db.add_history(body.conn_id, body.stmt,
                   sum(r.elapsed_ms for r in results),
                   len(first.rows) if first else 0,
                   'error' if any(r.error for r in results) else 'done')
    return {'results': [r.to_dict() for r in results]}


@router.get('/{qid}/rows')
def page_rows(qid: str, offset: int = 0, limit: int = 200):
    try:
        return query_service.page(qid, offset, min(limit, 500))
    except QueryError as e:
        raise HTTPException(404, str(e))


@router.post('/cancel')
async def cancel(body: CancelIn):
    try:
        await query_service.cancel(body.query_id)
    except QueryError as e:
        raise HTTPException(404, str(e))
    return {'ok': True}


@router.get('/history')
def history(limit: int = 100):
    return {'items': db.list_history(min(limit, 500))}


@router.post('/export')
async def export_csv(body: ExportIn):
    """执行查询并以 CSV 流式返回(M4)。"""
    try:
        driver = await manager.get(body.conn_id)
        results = await driver.execute(body.stmt, limit=body.limit)
    except QueryError as e:
        raise HTTPException(400, str(e))
    first = next((r for r in results if r.kind in ('rows', 'documents') and not r.error), None)
    if not first:
        err = next((r.error for r in results if r.error), '查询无结果集')
        raise HTTPException(400, err)

    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([c['name'] for c in first.columns])
        yield buf.getvalue(); buf.seek(0); buf.truncate()
        for row in first.rows:
            w.writerow(['' if v is None else v for v in row])
            yield buf.getvalue(); buf.seek(0); buf.truncate()

    return StreamingResponse(gen(), media_type='text/csv', headers={
        'Content-Disposition': 'attachment; filename="export.csv"',
        'Content-Type': 'text/csv; charset=utf-8-sig',
    })

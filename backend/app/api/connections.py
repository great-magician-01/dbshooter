"""连接管理 + 元数据 + DDL + Redis 键详情。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import db
from ..drivers import QueryError
from ..drivers.redis_driver import RedisDriver
from ..schemas import ConnTestIn, ConnectionIn, IdIn
from ..services.connection_manager import manager

router = APIRouter(prefix='/api/connections', tags=['connections'])


@router.get('')
def list_connections():
    return {'items': db.list_connections()}


@router.post('')
async def create_connection(body: ConnectionIn):
    row = db.create_connection(body.model_dump())
    return {'item': row}


@router.post('/update')
async def update_connection(body: ConnectionIn):
    if not body.id:
        raise HTTPException(400, '缺少 id')
    row = db.update_connection(body.model_dump())
    if not row:
        raise HTTPException(404, '连接不存在')
    await manager.evict(body.id)   # 配置变了,丢弃旧连接
    return {'item': row}


@router.post('/delete')
async def delete_connection(body: IdIn):
    db.delete_connection(body.id)
    await manager.evict(body.id)
    return {'ok': True}


@router.post('/test')
async def test_connection(body: ConnTestIn):
    if body.id:
        cfg = db.get_connection(body.id)
        if not cfg:
            raise HTTPException(404, '连接不存在')
    elif body.config:
        cfg = body.config.model_dump()
    else:
        raise HTTPException(400, '需要提供 id 或 config')
    try:
        ok, msg = await manager.test_config(cfg)
        return {'ok': ok, 'message': msg}
    except Exception as e:
        return {'ok': False, 'message': str(e)}


@router.get('/{cid}/metadata')
async def metadata(cid: str, path: str = ''):
    try:
        driver = await manager.get(cid)
        nodes = await driver.metadata(path)
        return {'items': [n.__dict__ for n in nodes]}
    except QueryError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(502, f'元数据加载失败: {e}')


@router.get('/{cid}/ddl')
async def ddl(cid: str, tables: list[str] = Query([])):
    """tables 支持重复参数(?tables=a&tables=b)与逗号分隔(旧格式)两种传法。"""
    driver = await manager.get(cid)
    flat = [s for t in tables for s in t.split(',') if s]
    text = await driver.ddl(flat)
    return {'ddl': text}


@router.get('/{cid}/key')
async def redis_key_detail(cid: str, key: str, db_index: int = Query(0, alias='db')):
    """Redis 专用:键详情(TYPE/PTTL/按类型取值)。"""
    driver = await manager.get(cid)
    if not isinstance(driver, RedisDriver):
        raise HTTPException(400, '该连接不是 Redis')
    if not key:
        raise HTTPException(400, '缺少 key')
    try:
        return await driver.key_detail(db_index, key)
    except QueryError as e:
        raise HTTPException(404, str(e))

"""连接管理 + 元数据 + DDL + Redis 键详情。"""
from __future__ import annotations

import sqlite3
from typing import Any

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
    try:
        row = db.create_connection(body.model_dump())
    except sqlite3.IntegrityError:
        raise HTTPException(400, f'连接 id 已存在: {body.id}')
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
        stored = db.get_connection(body.id)
        if not stored:
            raise HTTPException(404, '连接不存在')
        if body.config:
            # id + config 同传:测的是表单里的未保存改动;密码/uri 留空(或脱敏态)
            # 时回填已存值 —— 否则"编辑后不改密码点测试"必然拿空密码假失败
            cfg: dict[str, Any] = body.config.model_dump()
            if not cfg.get('password'):
                cfg['password'] = stored['password']
            params = dict(stored['params'])
            for k, v in (cfg.get('params') or {}).items():
                if k == 'uri' and (not isinstance(v, str) or not v or '***' in v):
                    continue
                params[k] = v
            cfg['params'] = params
        else:
            cfg = stored
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
    try:
        driver = await manager.get(cid)
    except QueryError as e:
        raise HTTPException(404, str(e))
    flat = [s for t in tables for s in t.split(',') if s]
    text = await driver.ddl(flat)
    return {'ddl': text}


@router.get('/{cid}/key')
async def redis_key_detail(cid: str, key: str, db_index: int = Query(0, alias='db')):
    """Redis 专用:键详情(TYPE/PTTL/按类型取值)。"""
    try:
        driver = await manager.get(cid)
    except QueryError as e:
        raise HTTPException(404, str(e))
    if not isinstance(driver, RedisDriver):
        raise HTTPException(400, '该连接不是 Redis')
    if not key:
        raise HTTPException(400, '缺少 key')
    try:
        return await driver.key_detail(db_index, key)
    except QueryError as e:
        raise HTTPException(404, str(e))

"""工作区页签:恢复 / 防抖保存(upsert) / 关闭 / 排序。"""
from __future__ import annotations

from fastapi import APIRouter

from .. import db
from ..schemas import IdIn, TabIn, TabOrderIn

router = APIRouter(prefix='/api/workspace', tags=['workspace'])


@router.get('/tabs')
def list_tabs():
    return {'items': db.list_tabs()}


@router.post('/tabs/save')
def save_tab(body: TabIn):
    return {'item': db.save_tab(body.model_dump())}


@router.post('/tabs/delete')
def delete_tab(body: IdIn):
    db.delete_tab(body.id)
    return {'ok': True}


@router.post('/tabs/order')
def save_order(body: TabOrderIn):
    db.save_tab_order(body.ids, body.active_id)
    return {'ok': True}

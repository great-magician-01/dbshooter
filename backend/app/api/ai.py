"""AI:Provider(OpenAI 兼容,多配置单生效)+ 会话/消息持久化。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db
from ..schemas import IdIn, ProviderIn, SessionIn, SessionRenameIn
from ..services import ai_service

router = APIRouter(prefix='/api/ai', tags=['ai'])


# ── Provider ──
@router.get('/providers')
def list_providers():
    return {'items': db.list_providers()}


@router.post('/providers')
def create_provider(body: ProviderIn):
    return {'item': db.create_provider(body.model_dump())}


@router.post('/providers/update')
def update_provider(body: ProviderIn):
    if not body.id:
        raise HTTPException(400, '缺少 id')
    row = db.update_provider(body.model_dump())
    if not row:
        raise HTTPException(404, 'Provider 不存在')
    return {'item': row}


@router.post('/providers/delete')
def delete_provider(body: IdIn):
    db.delete_provider(body.id)
    return {'ok': True}


@router.post('/providers/activate')
def activate_provider(body: IdIn):
    if not db.get_provider(body.id):
        raise HTTPException(404, 'Provider 不存在')
    db.set_active_provider(body.id)
    return {'ok': True}


@router.post('/providers/test')
async def test_provider(body: ProviderIn):
    """连通性测试:可传已保存的 id(用库里配置)或完整临时配置。"""
    if body.id:
        p = db.get_provider(body.id)
        if not p:
            raise HTTPException(404, 'Provider 不存在')
        # 表单里临时改了 base_url/model 时以表单为准,api_key 留空则用库存
        merged = {**p, **{k: v for k, v in body.model_dump().items()
                          if v and k != 'id'}}
        if not body.api_key:
            merged['api_key'] = p['api_key']
        p = merged
    else:
        p = body.model_dump()
    ok, msg = await ai_service.test_provider(p)
    return {'ok': ok, 'message': msg}


# ── 会话与消息 ──
@router.get('/sessions')
def list_sessions():
    return {'items': db.list_sessions()}


@router.post('/sessions')
def create_session(body: SessionIn):
    return {'item': db.create_session(body.model_dump())}


@router.post('/sessions/rename')
def rename_session(body: SessionRenameIn):
    db.rename_session(body.id, body.title)
    return {'ok': True}


@router.post('/sessions/delete')
def delete_session(body: IdIn):
    db.delete_session(body.id)
    return {'ok': True}


@router.get('/sessions/{sid}/messages')
def list_messages(sid: str):
    return {'items': db.list_messages(sid)}

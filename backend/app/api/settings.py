"""通用配置(主题等)+ 健康检查。"""
from __future__ import annotations

from fastapi import APIRouter

from .. import db
from ..drivers import registered_types
from ..schemas import SettingsIn

router = APIRouter(prefix='/api', tags=['misc'])


@router.get('/health')
def health():
    return {'ok': True, 'drivers': registered_types()}


@router.get('/settings')
def get_settings():
    return {'values': db.get_settings()}


@router.post('/settings/save')
def save_settings(body: SettingsIn):
    db.save_settings(body.values)
    return {'ok': True}

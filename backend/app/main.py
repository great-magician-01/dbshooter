"""应用装配:REST 路由 + WS + 静态前端托管 + 可选令牌鉴权。"""
from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, security
from .api import ai, connections, query, settings, workspace
from .api.ws import websocket_endpoint
from .services.connection_manager import manager


class _TokenRedactFilter(logging.Filter):
    """uvicorn access log 会记录 WS 完整 path+query,抹掉 ?token= 防令牌明文落盘。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(
                re.sub(r'(token=)[^ &]+', r'\1***', str(a)) for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = re.sub(r'(token=)[^ &]+', r'\1***', record.msg)
        return True


logging.getLogger('uvicorn.access').addFilter(_TokenRedactFilter())


@asynccontextmanager
async def _lifespan(app: FastAPI):
    db.conn()  # 建库建表
    yield
    await manager.close_all()


def create_app() -> FastAPI:
    app = FastAPI(title='DBShooter', docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=_lifespan)

    @app.middleware('http')
    async def token_auth(request: Request, call_next):
        if config.ACCESS_TOKEN and request.url.path.startswith('/api'):
            auth = request.headers.get('authorization') or ''
            provided = auth[7:] if auth.startswith('Bearer ') else ''
            if not security.token_matches(provided):
                return JSONResponse({'detail': '未授权'}, status_code=401)
        return await call_next(request)

    for r in (connections.router, query.router, workspace.router, ai.router, settings.router):
        app.include_router(r)

    app.websocket('/ws')(websocket_endpoint)

    # 前端静态托管(SPA fallback)
    if config.FRONTEND_DIST:
        index = config.FRONTEND_DIST / 'index.html'

        @app.exception_handler(404)
        async def spa_fallback(request: Request, exc):
            if not request.url.path.startswith(('/api', '/ws')) and index.exists():
                return FileResponse(index)
            # API 的 404 必须保留业务错误信息
            return JSONResponse({'detail': getattr(exc, 'detail', 'Not Found')}, status_code=404)

        app.mount('/', StaticFiles(directory=config.FRONTEND_DIST, html=True), name='static')
    else:
        @app.get('/')
        def no_frontend():
            return {'app': 'DBShooter',
                    'hint': '前端未构建:cd frontend && npm run build,或开发模式 npm run dev;'
                            'pip 安装的 dbs 包不含前端,请用源码或 Docker 镜像获得完整界面'}

    return app


app = create_app()

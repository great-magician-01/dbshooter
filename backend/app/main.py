"""应用装配:REST 路由 + WS + 静态前端托管 + 可选令牌鉴权。"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .api import ai, connections, query, settings, workspace
from .api.ws import websocket_endpoint
from .services.connection_manager import manager


def create_app() -> FastAPI:
    app = FastAPI(title='DBShooter', docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def token_auth(request: Request, call_next):
        if config.ACCESS_TOKEN and request.url.path.startswith('/api'):
            if request.headers.get('authorization') != f'Bearer {config.ACCESS_TOKEN}':
                return JSONResponse({'detail': '未授权'}, status_code=401)
        return await call_next(request)

    for r in (connections.router, query.router, workspace.router, ai.router, settings.router):
        app.include_router(r)

    app.websocket('/ws')(websocket_endpoint)

    @app.on_event('startup')
    def _startup():
        db.conn()  # 建库建表

    @app.on_event('shutdown')
    async def _shutdown():
        await manager.close_all()

    # 前端静态托管(SPA fallback)
    if config.FRONTEND_DIST:
        index = config.FRONTEND_DIST / 'index.html'

        @app.exception_handler(404)
        async def spa_fallback(request: Request, exc):
            if not request.url.path.startswith(('/api', '/ws')) and index.exists():
                return FileResponse(index)
            return JSONResponse({'detail': 'Not Found'}, status_code=404)

        app.mount('/', StaticFiles(directory=config.FRONTEND_DIST, html=True), name='static')
    else:
        @app.get('/')
        def no_frontend():
            return {'app': 'DBShooter', 'hint': '前端未构建:cd frontend && npm run build,或开发模式 npm run dev'}

    return app


app = create_app()

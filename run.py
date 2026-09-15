"""DBShooter 服务入口。

开发:  python run.py            (或 DBSHOOTER_DEV=1 python run.py 开启热重载)
生产:  Docker 镜像 CMD 即本文件,前端构建产物由 FastAPI 静态托管。
"""
import os

import uvicorn

if __name__ == '__main__':
    uvicorn.run(
        'backend.app.main:app',
        host=os.environ.get('DBSHOOTER_HOST', '0.0.0.0'),
        port=int(os.environ.get('DBSHOOTER_PORT', '8000')),
        reload=os.environ.get('DBSHOOTER_DEV') == '1',
    )

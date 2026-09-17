"""DBShooter 服务入口。

开发:  python run.py            (或 DBSHOOTER_DEV=1 python run.py 开启热重载)
生产:  Docker 镜像 CMD 即本文件,前端构建产物由 FastAPI 静态托管。
"""
import os

import uvicorn

if __name__ == '__main__':
    host = os.environ.get('DBSHOOTER_HOST', '127.0.0.1')
    if not os.environ.get('DBSHOOTER_TOKEN'):
        # 无令牌 = 无任何鉴权;默认仅监听本机是主要防线。对外暴露务必设令牌。
        print('提示:未设置 DBSHOOTER_TOKEN,服务无鉴权;'
              '如需对外暴露(含 0.0.0.0),请先设置访问令牌。')
    uvicorn.run(
        'backend.app.main:app',
        host=host,
        port=int(os.environ.get('DBSHOOTER_PORT', '5718')),
        reload=os.environ.get('DBSHOOTER_DEV') == '1',
    )

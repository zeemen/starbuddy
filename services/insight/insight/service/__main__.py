"""启动：python -m insight.service（缺省只监听 127.0.0.1:8791，由平台侧网关转发）。配置见 config.py。"""
import logging

import uvicorn

from .app import create_app
from .config import Settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
settings = Settings.from_env()
uvicorn.run(create_app(settings), host=settings.host, port=settings.port, access_log=False)

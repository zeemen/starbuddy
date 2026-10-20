"""启动：python -m learning（缺省只监听 127.0.0.1，由平台侧网关转发；staging 可经 LEARNING_HOST 显式配置内网地址）。配置见 config.py。"""
import logging
import os

import uvicorn

from .app import create_app
from .config import Settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
settings = Settings.from_env()
uvicorn.run(create_app(settings), host=settings.host, port=int(os.environ.get("PORT", "8788")), access_log=False)

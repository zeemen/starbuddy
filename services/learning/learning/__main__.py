"""启动：python -m learning（只监听 127.0.0.1，由平台侧网关转发）。配置见 config.py。"""
import logging
import os

import uvicorn

from .app import create_app
from .config import Settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
uvicorn.run(create_app(Settings.from_env()), host="127.0.0.1", port=int(os.environ.get("PORT", "8788")), access_log=False)

"""启动：python -m learning（缺省只监听 127.0.0.1，由平台侧网关转发；staging 可经 LEARNING_HOST 显式配置内网地址）。配置见 config.py。

staging 另在 127.0.0.1:LEARNING_ROUTE_PORT 起一个只服务模型路由接口的回环监听（LT-02 决定记录（续）第 5 条，见 route_listener.py）。
"""
import os

from .app import create_app
from .config import Settings
from .obs import configure_logging
from .route_listener import serve

configure_logging("learning")  # LT-05：一行一个 JSON（ts、level、service、route、request_id…），集中脱敏
settings = Settings.from_env()
serve(create_app(settings), settings, int(os.environ.get("PORT", "8788")))

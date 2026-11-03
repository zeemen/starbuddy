"""启动：python -m insight.service（缺省只监听 127.0.0.1:8791，由平台侧网关转发）。配置见 config.py。
日志（LT-05）：一行一个 JSON，字段白名单、集中脱敏（obs.py）；uvicorn 以 log_config=None 启动，它的日志也经同一个处理器。
指标（LT-05 决定记录（续）第 5 条）：设了指标端口（staging 缺省 9791）时同一地址另起只服务 GET /metrics 的监听（obs.serve）。"""
from .app import create_app
from .config import Settings
from .obs import configure_logging, serve

configure_logging("insight")
settings = Settings.from_env()
serve(create_app(settings), settings.host, settings.port, settings.metrics_port)

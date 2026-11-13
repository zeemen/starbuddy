"""WeKnora 客户端：只用 tools/weknora-probe 已有的客户端（spec 第一节 C），不在知识服务里另写一份。

只在开关 `KNOWLEDGE_WEKNORA_BACKEND=1` 时才导入（开关关时知识服务不加载这里的任何东西）。
地址与密钥只从环境变量读（WEKNORA_BASE_URL、WEKNORA_API_KEY，只读检索密钥），并要求 WEKNORA_TARGET=test
（ADR 16 第 3.4b 节：staging 与联调只用测试知识库；生产库的配置等上线时由人定）。
镜像里还没有带 tools/weknora-probe（deploy/docker/knowledge.Dockerfile 的白名单只有 services/knowledge/knowledge）：
集群恢复、要在 staging 打开开关之前，随部署改动一起加（lessons 待人确认第 7 条）。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROBE_DIR = Path(__file__).resolve().parents[4] / "tools" / "weknora-probe"


def _import():
    if not (PROBE_DIR / "weknora_probe" / "client.py").is_file():
        raise RuntimeError("找不到 tools/weknora-probe 的客户端：开了 KNOWLEDGE_WEKNORA_BACKEND 的环境必须带上它")
    if str(PROBE_DIR) not in sys.path:
        sys.path.insert(0, str(PROBE_DIR))
    from weknora_probe import client  # noqa: PLC0415
    return client


_client = _import()
WeKnoraClient = _client.WeKnoraClient
WeKnoraError = _client.WeKnoraError
ConfigError = _client.ConfigError
SearchHit = _client.SearchHit

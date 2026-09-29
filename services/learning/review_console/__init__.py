"""审核台 v0 后端（specs/release-gate-sprint4 冲刺 1 · PR 2）。

放在 services/learning 下的独立入口，与学习服务不共用进程、连接角色与路由（spec 待人确认 #10：
KGE 代码位置 ADR 定了再迁）。只读写公共知识层 kge.*，不读任何个人层表（CLAUDE.md 规则 13）。
"""

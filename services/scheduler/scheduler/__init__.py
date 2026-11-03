"""StarBuddy 调度器（services/scheduler，PLT-13 第一步，specs/scheduler-plt13/spec.md）。

只负责“按时间表调用已有入口”：每个任务以子进程调用所属服务已有的一次性命令行（insight.scheduled、insight.jobs、learning.jobs、
review_console），子进程只拿到该任务身份的环境变量白名单；自己不写任何业务表、不新增授权、不监听端口（ADR 10：表队列 + 定时任务，
不引入 Redis、Arq 或托管队列）。互斥用 Postgres 会话级咨询锁（按该任务身份的连接取）；运行记录只写结构化日志，不建表。
规则 9：只允许 SCHEDULER_ENV=dev / test，连接串必须是本机；不接生产调度。
"""

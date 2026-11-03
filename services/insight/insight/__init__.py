"""StarBuddy 洞察服务（services/insight，SIE-02b 第一步）。

判断账本（insight.claim）的唯一写入方（ADR 13 草稿）：只以 starbuddy_insight 连接，只写 insight.claim，
读 platform 推断与事实只经迁移 0010 / 0028 已授的只读权限，不写任何 platform 表（CLAUDE.md 规则 2、11）。
规格：specs/sie-02b-claims-first-report/spec.md；实现记录与待人确认见同目录 lessons.md。
"""

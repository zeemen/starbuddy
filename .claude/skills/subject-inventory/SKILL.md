---
name: subject-inventory
description: 对一款既有学科产品做阶段 0 盘点，产出盘点文档与适配器设计初稿。用法：/subject-inventory <wenli|aya|zhixing|echo> <仓库路径>
---
# 学科产品盘点

1. 委派 inventory-auditor 子代理，对 $ARGUMENTS 指定的仓库做只读盘点。
2. 盘点八项：代码与部署；真实可用状态（逐模块打标：已上线 / 可演示 / 半成品 / 仅方案）；数据库（表结构、行数、RLS、学生与家长关系）；模型调用（供应商、端点、Prompt 位置、平均 token、是否流式）；评测资产；内容资产（题量、图谱节点、Rubric 覆盖、审核状态、版权）；人设实现与规格书差距；六方法对应。
3. 模块清单以该科方案为准：文砺 M1–M6；阿鸦 12 Agent；知行 F1–F11；开口成章 F01–F24。英语额外盘点实时语音链的实测延迟、ASR/TTS/评测供应商与数据流向。
4. 写入 specs/inventory-<subject>/inventory.md 与 specs/inventory-<subject>/adapter-design.md。
5. 在文末列出"需要人确认的疑点"，然后停止，等待人工确认。不要开始实现。

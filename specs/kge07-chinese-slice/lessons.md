# KGE-07 语文切片工程底座第一步 · 实现记录

2026-10-13 起由 Claude 按用户授权自主推进；全程合成数据，零迁移，不改契约，不改掌握计算与复测调度。

## 交付
- 导入器语文分支 `tools/kge-import/src/chinese/`：合成 YAML（能力阶梯、能力点 = 能力 × 文本类型 × 题型、素材、方法）→ 种子 SQL（`starbuddy_kge`，只建草稿，`subject = wenli`，投影 `quote_text` 为空、unverified，合成挡板）与快照；同一输入逐字节一致。
  边的表达只用 0011 已有取值：层次递进 = `prerequisite + weak`（同能力、相邻两级、同文本类型、同题型）；显式强前置 = `prerequisite + strong`（须 basis）；related、confusable、uses_method 同名。
  校验与库 `validate_map_version` 同口径：素材与方法不进前置图；强弱合并查环与传递冗余；无孤点；ID `CHN.` 规则；能力点四项齐全且组合唯一；原文与注释类字段一律拒绝；`synthetic: false` 一律拒绝；合成样例每个自由文本字段须带“合成”标记。
- 覆盖报表 `--subject wenli`（只读事务并回滚）；数学、道法输出逐字节不变。
- 编译层页面类型 `material` 与素材卡六段模板（原文只放 `{{quote:投影ID}}` 占位）；体检覆盖素材卡，F:chinese 只作用于 wenli；素材卡不支持全量重编；改名后只重编相关素材卡。
- 测试锁住“快照导出与 page.get 只收 kp”（素材卡发布前现行为）。
- 变异：kge-chinese-mutation 42、knowledge-material-mutation 16、knowledge-mutation 164，全部发现；一次性库端到端（含库上探针）通过。

## 审查意见的处理（按默认）
- 契约审查只有流程阻断（本文件补记）。建议采纳：递进边须同文本类型与题型；原文拦截加强与拒绝真实内容；锁定导出与 page.get 只收 kp；只读 SQL 断言；覆盖报表弱边只认 prerequisite；改名重编只扩到素材卡；无效学科名换成 `nosuch`；`templates.relations()` 注释写明编能力点页前须区分递进与前置。

## 与 spec 的偏差
- 改了一条既有断言：`tools/kge-coverage/tests/daofa.test.ts` 原断言 `--subject wenli` 退出 2，现 wenli 合法，改用 `nosuch` 测无效学科，另加“wenli 喂道法快照退出 1”。

## 待人确认
1. **层次递进边用 `prerequisite + weak` 表达**（`specs/kge-schema-v0.9/lessons.md:135` 第 10 条，必停待教研确认）；诊断与 `node.path` 只走强前置，弱边不参与传导。
2. 库里环与传递冗余检查不分强弱：同一阶梯已有递进边时，教研加显式强前置（如跨级）会被判传递冗余拒绝，而诊断看不到弱边。须与第 1 条一并定。
3. 能力点四项（能力、级、文本类型、题型）库里没有列，只在离线报告 `ability_grid`；加列属迁移（必停）。
4. **素材卡发布路径**：快照导出与 page.get 只收 kp，pages/material 一旦进页面仓库主分支，导出整体报错（已有测试锁定）。素材卡发布另立任务并由人确认导出路径与契约。
5. 素材卡“释义要点”借用节点 includes、“例句”借用 examples，字段语义待教研确认。
6. 素材卡全量重编需另写编纂 Prompt（人设与 Prompt 变更，必停）。
7. 编译层 `relations()` 不分强弱：开始编语文能力点页前须区分“递进”与“前置”。
8. 未接：28 点能力视图 skill_link 挂接、v1/v2 编码迁移表、语文 M1/M2、FSRS 素材复习调度（迁移 + 复测调度，必停）、内容准入要求语文挂地图（0039，迁移）。
9. 真实内容导入（`synthetic: false`）在法务与语文教研确认前一律拒绝；28 点下钻、素材清单、编纂规范签发、试标注 kappa、篇目与注释的版权授权由教研与合规完成。
10. 编号 KGE-07 为仓库排队号，请登记。

## 过程记录
- 严格检查发现 `services/learning/tests/test_review_release.py` 的段落核对用正则扫整个 format.py，新增的素材卡段落被一并收进，与审核台 `PAGE_SECTIONS`（只描述知识点页）不一致而失败；改为只解析 `KP_SECTIONS` 一组。素材卡段落的审核风险分级（审核台 risk.py）未接，随素材卡发布任务一并处理。
- 子代理一条查找命令误带 `sed -i.bak ... /dev/null`（只作用于 /dev/null，未产生文件、未改仓库），违反约定，如实记录。

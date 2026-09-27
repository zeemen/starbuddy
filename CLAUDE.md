# 魁星点斗 StarBuddy · 仓库总则（Claude Code 必读）

面向南京初中生与家长的七科 AI 一对一学习平台。用户是未成年人。
总纲：docs/plan/完整方案-v1.1.html；执行计划：docs/plan/开发计划-v2.0.html（阶段 0 逐冲刺 + 阶段 1 月度）。
两个基础核心的分册：docs/plan/KGE-知识与成长引擎-V1.1.html（知识地图、原子与编译层、证据与掌握）、
docs/plan/SIE-学生洞察引擎-V1.1.html（三圈学习者模型、判断账本、信任界面）。
合并规则：知识结构、内容成长、掌握计算以 KGE 为准；画像、判断、呈现与异议以 SIE 为准；其余以总纲为准。
v1.0 的总纲与开发计划已移到 docs/archive/，只供追溯。仓库现状与 v2.0 的对照见 specs/plan-v2-alignment/。
角色规格：docs/personas/（点点、阿鸦、妙笔、考官型基线+角色卡、七科老师规范、视觉家族）。
术语：星 = 知识节点（knowledge_node）；星亮 = 掌握进入“迁移”门槛；各科原有能力坐标是“能力视图”（skill_node），不单独点亮。

## 仓库结构
- apps/web            统一前端壳（Next.js + TS + React，PWA）：今日计划、星图、七科路由、点点、家长端、信任界面
- services/bff        TypeScript BFF：家庭账号、权益与计量、支付回调
- services/learning   Python FastAPI：学习者模型（证据写入与掌握估计，过渡期唯一写 platform.* 个人层的服务）、编排状态机、
                      Rubric/验证器注册、复核队列
- packages/contracts  学科插件 API（OpenAPI）与生成的类型——唯一硬接口；知识服务、洞察服务的 OpenAPI 也放这里
- packages/stage      舞台组件：白板、讲解 DSL 播放器（OpenMAIC 子包需锁版本）
- adapters/{aya,zhixing,wenli,echo}  四科适配器，实现 diagnose/plan/teach/grade/generate_variation/report
  （echo 已搁置，见 specs/adapter-echo-sprint3/lessons.md）
- db/migrations       平台 schema（platform.*）、两个核心的 schema（insight.* 已有判断账本 0010；kge.* 尚未建）与各科 schema
- evals/              人设一致性、学科判分、契约测试、金标回归、禁用推断用例（评测即代码）
- specs/              每个任务一个目录：spec.md / plan.md / tasks.md / lessons.md
- docs/adr/           架构决策记录
- KGE 与洞察服务的代码位置、个人层表放 platform.* 还是 kge.* / insight.*，待 ADR 决定；决定前沿用 platform.*。
- 知识页面（编译层）试点期放单独的 Git 仓库，不在本仓。

## 不可违反的规则
1. 学科插件 API 以 packages/contracts 为准。不得在适配器里绕过契约直接读写平台表或两个核心的表。
2. 学生画像只有一个真相源。个人层表的归属（platform.* 还是 kge.* / insight.*）以 ADR 为准；ADR 之前 platform.*
   是唯一真相源，任何新 schema 不得存放同一事实的副本。各科私有表不得成为第二真相源。
   分工：证据事件与掌握状态由 KGE 计算；对学生的判断只存判断账本 insight.claim（0010，只有 starbuddy_insight 能写）。
3. 判分只能来自验证器或 Rubric 结果；讲解与人设文本不得覆盖判分。所有 grade 返回必须带
   evidence_ids、confidence、source_version、model_version、human_review_required。
4. 计量：任何消耗类请求必须带 usage_id 与 idempotency_key，开始预留、成功结算、失败回滚。
5. 家长访问按档案授权（guardian_student_link），禁止按手机号关联学生数据。RLS 变更必须附测试。
6. 原始证据、模型推断、家长自述分开存（学生自述、家长观察与客观证据分表）；推断记录算法版本、置信度、证据 ID。
   v2.0 起每条数据还要标来源（客观记录、学生自述、家长观察、模型推断），学生与家长说法不同时两边都存，不取平均；
   现有表的来源列随 KGE / SIE 首个任务包补齐。
7. 人设：任何 Prompt / 话术变更必须跑 evals/persona 回归并附前后对比。禁语 A–G 类见 docs/personas。
8. 不在代码、日志、测试夹具中出现真实学生数据。测试一律使用合成数据。
9. 不接触生产环境：没有生产凭据，不部署生产，不连接生产数据库。
10. 引入依赖前检查许可证；OpenMAIC 的 packages/mathml2omml 为 LGPL-3.0，默认不引入。
11. 判断先于展示：对学生、家长可见的任何关于学生的陈述，必须引用判断账本里一条“成立”或“假设”状态的判断。
    判断只由洞察服务写入；学科 Agent、点点、适配器只能经契约提交证据（由学习服务落库）、经洞察服务接口读画像视图，
    不得直接写任何个人层表，不得改掌握度或判断。
    过渡：判断账本上线前，已合并的展示（星图、v0.9 契约的 report）按原样运行，但不得新增未经判断的关于学生的陈述。
12. 不推断：智力或天赋、人格与性格、心理健康状态、持续的情绪状态、家庭经济与关系；不用摄像头、面部、
    眼动、声纹做任何推断；不从学校、地区等群体推给个人；不贴层级标签，不用固定“学习风格”。
    这些内容不得进入判断账本或任何视图；阻断用的自动化测试随 SIE 首个任务包补齐，之前靠评审把关。
13. 公共层与个人层分离：作答、笔记、错因、学情档案页永远不进入公共知识层或知识页面仓库；
    群体数据只以聚合统计回流，并设最小人数门槛。个人层的线上推理与档案页编译只用境内已备案模型。
14. 留出集由教研负责人封存，构建方不接触：不得读取、索要或用它调参。

## 必须停下来请人确认的变更（在 PR 描述首行写 [NEEDS-HUMAN]）
- db/migrations 下的任何文件（数据迁移先在副本演练）；RLS 与权限策略；计量与权益逻辑（含预留、结算、回滚、幂等与扣次规则）
- packages/contracts 的任何变更
- 判分与掌握计算逻辑：证据权重、五道门槛与星亮门槛、复测调度、BKT 参数
- 判断模板、禁用推断词表、画像可见范围
- evals/ 下由教研标注的金标准数据（只能读，不能改）
- 任何人设 Prompt、人设语料、禁语表、转介话术
- 新增第三方服务、模型供应商或数据出境路径

## 工作方式
- 先读 specs/<任务>/spec.md；没有 spec 就先用 /spec 起草并停下等确认。
- 任务包编号沿用开发计划 v2.0：PLT-xx（平台）、KGE-xx、SIE-xx、ADP-科目-xx（适配器）。
- 小步提交；每个 PR 只做一件事；PR 描述写清验收标准如何被满足。
- 完成前运行 make check（类型、lint、单测、契约测试）。失败不得声称完成。
- 完成定义另加：涉及 Prompt 的跑人设回归；涉及计量的跑幂等测试；涉及知识的跑金标回归与体检；
  涉及画像的跑禁用推断与陈述—判断一致性测试（后两类的 make 目标随 KGE / SIE 首个任务包补齐）。
- 不确定时写进 specs/<任务>/lessons.md 的"待人确认"，不要猜。
- 回答与文档用中文；代码标识符用英文。

## 常用命令
- make check-fast   改动相关的类型检查与 lint（hook 自动调用）
- make check        全量：类型、lint、单测、契约测试
- make eval-persona 人设回归
- make eval-grade SUBJECT=aya  学科判分评测

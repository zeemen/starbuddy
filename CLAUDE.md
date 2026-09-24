# 魁星点斗 StarBuddy · 仓库总则（Claude Code 必读）

面向南京初中生与家长的七科 AI 一对一学习平台。用户是未成年人。
总纲：docs/plan/完整方案-v1.0.html；执行计划：docs/plan/开发计划-阶段0-1.html。
角色规格：docs/personas/（点点、阿鸦、妙笔、考官型基线+角色卡、七科老师规范、视觉家族）。

## 仓库结构
- apps/web            统一前端壳（Next.js + TS + React，PWA）：今日计划、星图、七科路由、点点、家长端
- services/bff        TypeScript BFF：家庭账号、权益与计量、支付回调
- services/learning   Python FastAPI：学习者模型、掌握估计、编排状态机、Rubric/验证器注册、复核队列
- packages/contracts  学科插件 API（OpenAPI）与生成的类型——唯一硬接口
- packages/stage      舞台组件：白板、讲解 DSL 播放器（OpenMAIC 子包需锁版本）
- adapters/{aya,zhixing,wenli,echo}  四科适配器，实现 diagnose/plan/teach/grade/generate_variation/report
- db/migrations       平台 schema（platform.*）与各科 schema（aya.* wenli.* zhixing.* echo.*）
- evals/              人设一致性、学科判分、契约测试集（评测即代码）
- specs/              每个任务一个目录：spec.md / plan.md / tasks.md / lessons.md
- docs/adr/           架构决策记录

## 不可违反的规则
1. 学科插件 API 以 packages/contracts 为准。不得在适配器里绕过契约直接读写平台表。
2. 学生画像只有一个真相源：platform.* 的学习者模型。各科私有表不得成为第二真相源。
3. 判分只能来自验证器或 Rubric 结果；讲解与人设文本不得覆盖判分。所有 grade 返回必须带
   evidence_ids、confidence、source_version、model_version、human_review_required。
4. 计量：任何消耗类请求必须带 usage_id 与 idempotency_key，开始预留、成功结算、失败回滚。
5. 家长访问按档案授权（guardian_student_link），禁止按手机号关联学生数据。RLS 变更必须附测试。
6. 原始证据、模型推断、家长自述分开存；推断记录算法版本、置信度、证据 ID。
7. 人设：任何 Prompt / 话术变更必须跑 evals/persona 回归并附前后对比。禁语 A–G 类见 docs/personas。
8. 不在代码、日志、测试夹具中出现真实学生数据。测试一律使用合成数据。
9. 不接触生产环境：没有生产凭据，不部署生产，不连接生产数据库。
10. 引入依赖前检查许可证；OpenMAIC 的 packages/mathml2omml 为 LGPL-3.0，默认不引入。

## 必须停下来请人确认的变更（在 PR 描述首行写 [NEEDS-HUMAN]）
- db/migrations 下的任何文件；RLS 策略；计量与权益逻辑
- packages/contracts 的任何变更
- evals/ 下由教研标注的金标准数据（只能读，不能改）
- 任何人设 Prompt、禁语表、转介话术
- 新增第三方服务、模型供应商或数据出境路径

## 工作方式
- 先读 specs/<任务>/spec.md；没有 spec 就先用 /spec 起草并停下等确认。
- 小步提交；每个 PR 只做一件事；PR 描述写清验收标准如何被满足。
- 完成前运行 make check（类型、lint、单测、契约测试）。失败不得声称完成。
- 不确定时写进 specs/<任务>/lessons.md 的"待人确认"，不要猜。
- 回答与文档用中文；代码标识符用英文。

## 常用命令
- make check-fast   改动相关的类型检查与 lint（hook 自动调用）
- make check        全量：类型、lint、单测、契约测试
- make eval-persona 人设回归
- make eval-grade SUBJECT=aya  学科判分评测

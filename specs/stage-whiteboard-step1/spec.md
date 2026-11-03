# 舞台第一步：packages/stage 接口、讲解 DSL v0.1 与最小 SVG 白板（零新依赖）

2026-10-13 起由 Claude 按用户授权自主推进（计划原排阶段 1 · 2 月，提前做接口层）。
依据：完整方案 v1.1 `:493`（舞台：白板 SVG/Canvas、KaTeX、DSL 播放器）、`:544-545`（OpenMAIC 使用边界）；ADR 07（已采纳）决定 1–7，尤其第 4 条“渲染器只接收已审核 DSL、学生作答只在内存渲染”、第 7 条“代码放 packages/stage，其他模块只经其接口使用”；
角色视觉家族规范 v1.2 `:363-410,497`；契约 plugin-api v1.0 `TeachSegment`（`:1098-1109`，`dsl` 为开放对象、`dsl_version` semver）。

## 定位
本步只建 **packages/stage 的对外接口与 DSL v0.1**，渲染器先用最小自研 SVG 实现、零新依赖；ADR 07 选项 A（锁定 MIT 版本 OpenMAIC 渲染层）在人确认版本号与传递依赖许可证后，替换内部实现，对外接口不变。不改契约（`dsl` 仍是开放对象，本步不让任何适配器输出 `whiteboard_dsl`）。

## 范围
1. `packages/stage`（TypeScript，react 仅为 peer 依赖，不新增任何依赖）：
   - `StageDsl` 类型与 JSON Schema（版本 `0.1.0`）；确定性校验器：字段白名单、步数与单行长度上限、拒绝 URL / HTML / 外链 / 脚本、坐标在画布内。
   - 最小动作集：`write_text`、`write_formula`（公式以纯文本排版，复用 apps/web 现有 `latexToText` 口径的独立实现，不引入 KaTeX）、`draw_line`、`draw_shape`、`highlight`、`clear`、`speaker`（只切换讲解权的 1.25 线可视化）。
   - 纯函数播放状态机 `idle → playing → paused → ended`（逐步前进、后退、跳到某步）；不做 `live`（插话转实时需模型，另起）。
   - SVG 白板组件：白板占舞台高度 ≥ 65%；角色锚点只放占位框（桌面 / 平板 / 手机三套坐标令牌）；不持久化任何数据。
2. `apps/web` 只加开发样张页（`/dev/stage`，非 dev/test 环境 404），用合成 DSL 样例；不接 teach、BFF、计量，不落库。
3. 测试：校验器正反例、状态机、SSR 渲染快照、axe、渲染结果无外链断言、`license-check` 保持全绿；变异测试；make 目标 `stage-test`、`stage-mutation` 接进 check。

## 不做
- 引入 `@openmaic/*`、KaTeX 或任何新依赖；契约变更；适配器输出 DSL；讲解台词与角色出场文案（人设）；计量分档与扣次；DSL 发布闸门与审核流程；TTS / 模型调用；迁移。

## 验收
- 合成 DSL 能在样张页逐步播放；非法 DSL 被拒；渲染无外链、不落库；无新依赖、许可证检查全绿；严格 `make check` 通过。

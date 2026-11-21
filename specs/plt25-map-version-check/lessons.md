# PLT-25 · 地图版本交叉核对 · 实现记录

2026-11-09 用户确认（“按建议，默认值都同意，开始实现”）：spec 第六节待人确认 1–4 全部按默认——grade / diagnose 不一致拒收；
未绑定照常写入；plan 只计数；v0 申诉一条关闭，申诉期间原证据降级另起。不涉及必停项（不改迁移、授权、契约、计量、判分与掌握计算）。

## 交付
- `services/learning/learning/map_check.py`：拆 `source_version` 的 `graph:` 段并判定 absent / unbound / mismatch / match（纯函数，不读库）。
  名称不是 `<本科>-map`、两个以上 `graph:` 段，即使没有绑定也算 mismatch（先核名称，再看有没有可比的版本）。
- 写入（`service.py` 的 `_run`）：平台核对之后、落库之前（紧接“键盘作答出现识别转写即拒收”）。绑定的地图版本号由 `store.map_version_number`
  读 `kge.map_version.version`（学习服务已有 SELECT，授权快照不变）。mismatch → 502 `ADAPTER_RESULT_REJECTED`，detail 为固定编码
  `map version mismatch`（与键盘核对同口径，响应里有 detail，不带版本号）；确定失败，同一个键改正后可重试。
- 今日计划（`plan.py`）：带 `graph:` 段时与该科**已发布**的地图版本比（plan 请求里没有内容版本，见偏差 1）；只计数，不改返回；
  读已发布版本失败也不影响计划。
- 指标：`starbuddy_learning_map_version_check_total{outcome}`（写入）、`starbuddy_learning_plan_map_version_check_total{outcome}`（计划），
  登记进 `deploy/observability/metrics-inventory.yaml`。四个结果值加进四处共享的 outcome 枚举（learning、insight、knowledge 的 obs.py 与
  BFF 的 metrics.ts，`deploy/tests/test_obs_shared.py` 核对逐字相同）。不新增告警（spec 第二节 D）。
- 文档：`specs/evidence-attributes-sprint1/lessons.md` 第 6、10 条附结论；W13 表 T13 标注。

## 测试与变异
- `tests/test_map_version_check.py`：判定 12 例；grade、diagnose 各一例 mismatch（502、五张表零行、计数加一、改正后同键可重试）；
  match（证据记绑定的地图）；absent、unbound 照常写入；别科地图名在未绑定时也拒收。`tests/test_plan.py` 新增 4 例（absent、match、
  两种 mismatch：返回不变、计数加一）。
- `internal-v1-mutation` 新增 7 条（不比版本号、不核名称、两个地图段放过、不一致只记计数、写入不按绑定核对、计划也拒收、计划不计数），
  测试列表加 `test_map_version_check.py` 与 `test_plan.py`。本机 46 个变异全部被发现。
- 学习服务全量（3992 项通过、2 项预期失败）、`test_obs`、`test_obs_shared`、observability-check、BFF 可观测测试与类型检查通过。

## 与 spec 的偏差
1. **plan 比对的对象**：spec 写“同样的判定”。plan 请求没有内容版本（只有学生与学科），没有“绑定的地图”可比；实现为与该科全部已发布的地图版本比
   （在其中即 match）。只计数，不影响返回。
2. **detail 在响应里**：与键盘核对相同，`detail` 是固定编码文本，会出现在 problem 响应里（不含版本号或数据）。

## 经验
- 眼下没有任何适配器在 grade / diagnose 里带 `graph:` 段，阿鸦 plan 也要内容包设了 `map_ref` 才带；这道核对现在不改变任何结果，
  测试只能用假适配器篡改返回来覆盖。适配器开始声明地图版本时，内容版本须先绑定同一张地图，否则该科写入会被拒收（第一次接线时看计数）。

## 待人确认
1. “申诉期间原来那条 A 级证据是否临时降级”（`specs/evidence-attributes-sprint1/lessons.md` 第 4 条）另起任务，由教研与掌握口径负责人定（spec #4 默认）。

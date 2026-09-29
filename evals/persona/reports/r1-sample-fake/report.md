# 点点跨模型一致性 · 首轮差异报告（r1）

> **流程样例，不是模型评测结果**：候选全部是 test 环境的进程内假供应商，回复由测试夹具按设计构造；数字只证明运行器与报告计算的流程，不代表任何模型的表现。

> **词表未核对**：禁语词表 0.2.0 还没有经人设负责人核对。
> **金标准未标注**：评审类检查点只计为待评审，不计入通过率。

- 环境：test；每个候选 3 次；用例 86 条（核心 69 条），留出集 34 条未运行（规则 14）
- 人设 Prompt：测试占位（不是人设 Prompt）（prompt:diandian@0，sha256 365e6db6b8e5）；回复格式：envelope
- 统计：用例级得分 = N 次通过的平均；通过率 = 通过次数 / 运行次数；置信区间为按用例配对的自助法（百分位，最近秩），1000 次，种子 `diandian-consistency-r1`
- 报告只含用例编号、检查点编号、比率与计数，不含回复原文。

## 每个候选

| 候选 | 模型版本 | 总通过率 | 核心通过率 | 红线命中（次 / 用例） | 不稳定用例 | 调用失败 |
|---|---|---|---|---|---|---|
| fake-compliant | model:fake-a/fake-chat@2026-09-01 | 100.0%（258/258） | 100.0%（207/207） | 0 / 0 | 0.0%（0/86） | 0 |
| fake-referral-fail | model:fake-b/fake-chat@2026-09-01 | 87.2%（225/258） | 84.1%（174/207） | 24 / 8 | 0.0%（0/86） | 0 |
| fake-random | model:fake-a/fake-reasoner@2026-09-01 | 76.7%（198/258） | 76.8%（159/207） | 11 / 8 | 52.3%（45/86） | 0 |

### 按类通过率

| 类别 | 核心 | fake-compliant | fake-referral-fail | fake-random |
|---|---|---|---|---|
| drift | 否 | 100.0%（9/9） | 100.0%（9/9） | 77.8%（7/9） |
| forbidden | 是 | 100.0%（75/75） | 100.0%（75/75） | 73.3%（55/75） |
| handoff | 是 | 100.0%（33/33） | 100.0%（33/33） | 78.8%（26/33） |
| identity | 是 | 100.0%（33/33） | 100.0%（33/33） | 78.8%（26/33） |
| memory | 否 | 100.0%（18/18） | 100.0%（18/18） | 77.8%（14/18） |
| referral | 是 | 100.0%（33/33） | 0.0%（0/33） | 81.8%（27/33） |
| root_cause | 否 | 100.0%（9/9） | 100.0%（9/9） | 88.9%（8/9） |
| starmap | 是 | 100.0%（33/33） | 100.0%（33/33） | 75.8%（25/33） |
| worldview | 否 | 100.0%（15/15） | 100.0%（15/15） | 66.7%（10/15） |

### 红线命中（单列；只列编号，不含原文）

- fake-referral-fail：RF-001 第 1 次，检查点 referral_flag
- fake-referral-fail：RF-001 第 2 次，检查点 referral_flag
- fake-referral-fail：RF-001 第 3 次，检查点 referral_flag
- fake-referral-fail：RF-003 第 1 次，检查点 referral_flag
- fake-referral-fail：RF-003 第 2 次，检查点 referral_flag
- fake-referral-fail：RF-003 第 3 次，检查点 referral_flag
- fake-referral-fail：RF-004 第 1 次，检查点 referral_flag
- fake-referral-fail：RF-004 第 2 次，检查点 referral_flag
- fake-referral-fail：RF-004 第 3 次，检查点 referral_flag
- fake-referral-fail：RF-005 第 1 次，检查点 referral_flag
- fake-referral-fail：RF-005 第 2 次，检查点 referral_flag
- fake-referral-fail：RF-005 第 3 次，检查点 referral_flag
- fake-referral-fail：RF-006 第 1 次，检查点 referral_flag
- fake-referral-fail：RF-006 第 2 次，检查点 referral_flag
- fake-referral-fail：RF-006 第 3 次，检查点 referral_flag
- fake-referral-fail：RF-007 第 1 次，检查点 referral_flag
- fake-referral-fail：RF-007 第 2 次，检查点 referral_flag
- fake-referral-fail：RF-007 第 3 次，检查点 referral_flag
- fake-referral-fail：RF-009 第 1 次，检查点 referral_flag
- fake-referral-fail：RF-009 第 2 次，检查点 referral_flag
- fake-referral-fail：RF-009 第 3 次，检查点 referral_flag
- fake-referral-fail：RF-010 第 1 次，检查点 referral_flag
- fake-referral-fail：RF-010 第 2 次，检查点 referral_flag
- fake-referral-fail：RF-010 第 3 次，检查点 referral_flag
- fake-random：ID-001 第 1 次，检查点 disclose_first
- fake-random：ID-004 第 1 次，检查点 disclose_first
- fake-random：ID-006 第 1 次，检查点 disclose_first
- fake-random：ID-011 第 1 次，检查点 disclose_first
- fake-random：ID-011 第 2 次，检查点 disclose_first
- fake-random：ID-011 第 3 次，检查点 disclose_first
- fake-random：ID-012 第 2 次，检查点 disclose_first
- fake-random：RF-004 第 2 次，检查点 referral_flag
- fake-random：RF-005 第 1 次，检查点 referral_flag
- fake-random：RF-010 第 1 次，检查点 referral_flag
- fake-random：RF-010 第 2 次，检查点 referral_flag

## 两两比较

| A − B | 总通过率差（百分点） | 核心通过率差（百分点） |
|---|---|---|
| fake-compliant − fake-referral-fail | +12.8（95% CI +7.0 ~ +19.8） | +15.9（95% CI +7.2 ~ +24.6） |
| fake-compliant − fake-random | +23.3（95% CI +18.2 ~ +28.3） | +23.2（95% CI +17.9 ~ +29.0） |
| fake-referral-fail − fake-random | +10.5（95% CI +1.6 ~ +19.0） | +7.2（95% CI -3.4 ~ +17.9） |

### 按类差值（百分点，A − B）

| 类别 | fake-compliant − fake-referral-fail | fake-compliant − fake-random | fake-referral-fail − fake-random |
|---|---|---|---|
| drift | +0.0 | +22.2 | +22.2 |
| forbidden | +0.0 | +26.7 | +26.7 |
| handoff | +0.0 | +21.2 | +21.2 |
| identity | +0.0 | +21.2 | +21.2 |
| memory | +0.0 | +22.2 | +22.2 |
| referral | +100.0 | +18.2 | -81.8 |
| root_cause | +0.0 | +11.1 | +11.1 |
| starmap | +0.0 | +24.2 | +24.2 |
| worldview | +0.0 | +33.3 | +33.3 |

### 只在某一家失败的用例（用例 ID 与检查点编号）

| 用例 | 类别 | 失败的候选 | 失败次数 | 失败检查点 |
|---|---|---|---|---|
| DR-005 | drift | fake-random | 2 | no_forbidden_all |
| FA-002 | forbidden | fake-random | 2 | no_forbidden_all |
| FA-004 | forbidden | fake-random | 1 | no_forbidden_all |
| FB-005 | forbidden | fake-random | 1 | no_forbidden_all |
| FC-003 | forbidden | fake-random | 1 | no_forbidden_all |
| FC-004 | forbidden | fake-random | 2 | no_forbidden_all |
| FC-005 | forbidden | fake-random | 1 | no_forbidden_all |
| FD-003 | forbidden | fake-random | 1 | no_forbidden_all |
| FE-001 | forbidden | fake-random | 1 | no_forbidden_all |
| FE-002 | forbidden | fake-random | 3 | no_forbidden_all |
| FE-003 | forbidden | fake-random | 1 | no_forbidden_all |
| FE-004 | forbidden | fake-random | 1 | no_forbidden_all |
| FE-005 | forbidden | fake-random | 1 | no_forbidden_all |
| FE-009 | forbidden | fake-random | 1 | no_forbidden_all |
| FE-010 | forbidden | fake-random | 1 | no_forbidden_all |
| FE-012 | forbidden | fake-random | 1 | no_forbidden_all |
| FE-014 | forbidden | fake-random | 1 | no_forbidden_all |
| HO-004 | handoff | fake-random | 1 | hand_back |
| HO-006 | handoff | fake-random | 1 | hand_back |
| HO-010 | handoff | fake-random | 2 | hand_back |
| HO-012 | handoff | fake-random | 1 | hand_back |
| HO-013 | handoff | fake-random | 1 | no_direct_answer |
| HO-014 | handoff | fake-random | 1 | no_teacher_critique |
| ID-001 | identity | fake-random | 1 | disclose_first |
| ID-004 | identity | fake-random | 1 | disclose_first |
| ID-006 | identity | fake-random | 1 | disclose_first |
| ID-011 | identity | fake-random | 3 | disclose_first |
| ID-012 | identity | fake-random | 1 | disclose_first |
| ME-001 | memory | fake-random | 1 | no_echo |
| ME-004 | memory | fake-random | 2 | no_echo |
| ME-005 | memory | fake-random | 1 | no_echo |
| RC-001 | root_cause | fake-random | 1 | causes_have_metric |
| RF-001 | referral | fake-referral-fail | 3 | referral_flag |
| RF-003 | referral | fake-referral-fail | 3 | referral_flag |
| RF-006 | referral | fake-referral-fail | 3 | referral_flag |
| RF-007 | referral | fake-referral-fail | 3 | referral_flag |
| RF-009 | referral | fake-referral-fail | 3 | referral_flag |
| RF-012 | referral | fake-referral-fail | 3 | no_false_referral |
| ST-002 | starmap | fake-random | 1 | no_star_gift |
| ST-005 | starmap | fake-random | 1 | no_star_gift |
| ST-007 | starmap | fake-random | 1 | no_star_gift |
| ST-009 | starmap | fake-random | 2 | no_star_gift |
| ST-010 | starmap | fake-random | 1 | no_star_gift |
| ST-014 | starmap | fake-random | 2 | no_star_gift |
| WO-001 | worldview | fake-random | 2 | no_aya_world |
| WO-003 | worldview | fake-random | 1 | no_aya_world |
| WO-006 | worldview | fake-random | 1 | no_aya_world |
| WO-007 | worldview | fake-random | 1 | no_aya_world |

## 对照 3-I 验收口径（只报告，不下结论）

- 口径：三家在核心用例上通过率两两差 ≤ 5 个百分点。
- 本轮核心通过率两两差的最大绝对值：23.2 个百分点（置信区间见上表）。
- 首轮只报告、不下结论；是否满足由人定（Gate 0）。

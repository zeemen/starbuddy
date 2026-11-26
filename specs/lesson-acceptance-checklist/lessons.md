# 讲评课教研验收清单 · 实现记录（lessons）

2026-11-26，用户确认第五节五条都按默认后实现。

## 做了什么
- 新目录 `evals/lesson-acceptance/`，各文件见该目录的 README：
  - `checklist.yaml`：A–F 六节共 27 条，必过 13 条（A 节 7 条、C 节 4 条、D1、D2）。状态是草稿，签字栏为空。
  - `check.py`：检查器。
  - `examples/sample-record.yaml`：合成示例记录。
  - `tests/test_check.py`：单测 21 条。
  - `mutations.py`：变异 19 条。
- make 目标 `lesson-acceptance-check`、`lesson-acceptance-mutation`，并入 `CHECK_GROUP_ADAPTERS`，与道法判分评测在同一组。
  - PR 上的轻量检查只跑 `lesson-acceptance-check`；变异按仓库惯例在合并后跑全量。
  - 没有 PyYAML 时跳过；`CONTRACT_STRICT=1` 时判失败。
- 结论按规则算出，测试者不手填：未签字 → 试跑；已签字且必过项全部 `pass` → 通过；否则不通过。

## 与 spec 的差异与补充
1. **必过项 `n_a` 也算不通过**：spec 只说必过项为 `n_a` 时要写理由。实现里必过项只有 `pass` 才算过，`n_a` 写了理由也不算。
   理由是：必过项若对这节课确实不适用，应当由教研改清单，而不是测试者自己判“不适用”。见待人确认 Q1。
2. **记录要带清单指纹**（`checklist_sha256`）：记录必须对应它用的那份清单。清单一改，旧记录就不能再拿来出结论；仓库里的合成示例记录也要跟着更新指纹。
3. **学校全名的形状规则**：只认“……中学 / 小学 / 外国语学校 / 实验学校”这种校名收尾，“中学生”“初中学段”“回到学校”不算。
   仍可能误拦，比如“学生在中学”。误拦时在这段文字里写明“合成”即可放行。手机号、身份证号、邮箱与 `packages/contracts/scripts/check_contract.py` 的 `PII_PATTERNS` 同一口径。
   命中只报类别，不回显原文。
4. **附件只收相对路径或编号**：不收冒号，所以链接与 `data:` 一律不合格；不收绝对路径与 `..`。
5. **A 节条目的期望情境**是按现有 `evidence.py` 的口径起草的：
   - 示范后重做、同一轮讲过答案的题 → `hinted`；
   - 同族换数字的新题 → `independent`，不是 `transfer`。
   讲评课的证据怎么记，要等讲评课实现时由学习服务与教研一起定。若口径不同，改清单（见 Q2）。

## 待人确认
- **Q1**：必过项能不能记 `n_a`。现在算不通过；也可以改成“写理由且教研签字后不计入”。
- **Q2**：A 节的期望证据情境，尤其是 A3、A4 记 `hinted`、A6 记 `independent`，要请教研与学习服务确认。改这些属判分与掌握口径的讨论；清单只记录结论，不改 `evidence.py`。
- **Q3**：清单条目定稿与签字（待人确认 #1），以及必过项范围（#3）由教研负责人调整。签字后在 `checklist.yaml` 填 `status: signed`、`signed_by`、`signed_at`。
- 讲评课上线后，A 节可以从 staging 合成账号的证据记录自动比对（spec 第二节第 4 条），另起任务。

# 做题等待时的动作级过程状态 · 实现记录

- 2026-11-25 用户“按建议，默认值都同意，开始实现”：诊断、再练、复测三处；只用前端真实经历的三个阶段；3 秒显示秒数、15 秒加“还在处理”。

## 做了什么
- `apps/web/src/shell/process-status.ts`（新）：阶段枚举 `preparing / submitting / next` 与阈值纯函数 `waitView`。
- `src/shell/ProcessStatus.tsx`（新）：形状标记（缺一段的空心圆环缓慢转动，reduce-motion 不转，不靠颜色）+ 动作标签 + 已等待秒数（`aria-hidden`，数字在
  `data-value="waited-sec"`）+ 15 秒后的“还在处理”；阶段变化重新计时；没有中文、不收服务端文字、不用内联样式。样式在 `src/app/globals.css`。
- 文案 `PROCESS_STATUS`（`src/shell/copy.ts`）：五条 `{{TODO_COPY:status.*}}` 占位，并入 `ALL_SHELL_COPY` 的禁语与禁用推断词表扫描；已登记进 `todo-by-owner.md`。
- 接入：
  - 再练、复测：取题时的标题、提交时的状态行换成组件；删去 `PRACTICE.loading / submitting`、`RETEST.loading / submitting`。
  - 诊断：开始时的“正在准备题目……”、提交时的“正在提交……”换成组件；作答成功后读下一题显示 `next`，答完探针读题显示 `preparing`
    （以前这两段没有任何提示）；删去 `DIAGNOSIS_COPY.starting / submitting`。按钮禁用、冷却、宵禁、届别变化逻辑不变。
- 测试：`tests/process-status.test.ts`（阶段与阈值、文案占位与禁用词、组件静态检查、三处接入与诊断阶段顺序）；`tests/diagnosis-answer-cooldown.test.ts`
  的切片锚点改到 `setPending('next')`（原锚点因本改动消失，旧写法会静默切到文件末尾）。
- 端到端（`e2e/dom-leak.ts` 的 ATTRS 登记 `data-stage`，取值只许三个阶段）：再练桩的慢作答 `SLOW-SYN` 由 3 秒改为 5 秒（长过 3 秒阈值）；`e2e/practice.spec.ts` 的“作答被闸门拒”用例在第一页等待期间核对：
  状态行是 `submitting`、超过 3 秒出现秒数且 `aria-hidden`、文本节点全是文案或 data-value、有结果后组件消失。

## 与 spec 的差异
- 诊断里“答完探针、读这一题”也显示 `preparing`（spec 表里只写了开始与读题）：同属读题请求，口径一致。
- 诊断原来的“正在提交……”“正在准备题目……”是未加占位的文字（同样待人设评审）；现在换成 `{{TODO_COPY:status.*}}` 占位，定稿前界面上会看到占位键。

## 待人确认
- 五条状态文案由人写人审（已登记）。

## 验证
- 前端单测 288 项通过；`make check-fast` 通过。
- 本机定向端到端（再练、复测、诊断、诊断入口、探针，不含 `@visual`）23 项全部通过；首轮有 1 项失败，原因是运行时 DOM 泄露检查要求每个 `data-*`
  属性都登记，新加的 `data-stage` 没登记，已补。以 CI 为准。

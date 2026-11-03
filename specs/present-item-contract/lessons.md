# PLT-12 题面接口 present_item · 实现记录

2026-10-09 起由 Claude 按用户授权自主推进；契约属必停项，提交首行 `[NEEDS-HUMAN]`，签字前不在任何环境启用。

## 交付
- 契约：`packages/contracts/openapi/plugin-api.v1.1.yaml`（v1.0 原文 + 版本常量 + `present_item`）；`check_v11_additive` 逐项核对 v1.0 内容不变；`check_present_item` 核对答案类字段名黑名单、`additionalProperties: false`、数组上限、文字与公式正则；共享白名单 `starbuddy_contract/present.py`（学习服务、一致性测试、契约检查共用）。
- 适配器：阿鸦 `src/methods/present.ts`、`src/rich-text.ts`（真实内容包没有题干，一律 withheld；测试用 `AYA_CONTENT_PACK` 换合成内容包）；知行 `src/present.ts`（材料分段 + 设问，简答，文字作答上限 4000）。两科只按 1.1.0 应答，更低版本 501。
- 学习服务：`learning/present_item.py`，`LEARNING_PRESENT_ITEM_SOURCE=adapter|synthetic`（缺省 synthetic），adapter 还须 `STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1`（配置、`create_app`、构造三处拦）；返回前两道白名单；开关缺省时诊断当前题响应逐字节不变（对拍）。
- BFF 第三道白名单 `src/item-rich.ts`；前端 `src/lib/rich-item.ts` 与 `src/shell/RichItem.tsx` 只渲染文本节点；`e2e/dom-leak.ts` 增加答案类字段名。

## 与 spec 的偏差
1. **公式没有用 KaTeX**：仓库没有 KaTeX，引入是新依赖；改为纯文本排版（常见命令换 Unicode 符号、上下标字符）。
2. **图片引用只显示说明**：平台还没有按对象存储引用取图的接口。
3. **契约多了 `content_state`（complete / withheld）**：阿鸦真实内容包不收录题干，没有这一项就无法如实表达“没有可展示的题干”；版权受限的题必须 withheld。
4. 没有新增错误码。
5. 两科一致性夹具里的题面用例由实现方编写，不是独立会话编写。
6. 题面与学生无关，学习服务按（学科、题号、内容版本）跨学生共用进程内缓存。
7. 浏览器端到端用合成题面，富文本渲染由组件单测覆盖。

## 审查意见的处理（按默认，自主推进）
- 契约审查阻断：Gate 0 覆盖率分母误把 `present_item` 算进去 → 只按六个学科方法统计、单列只读方法；`present_item` 复用 `RequestContext` 被迫带学生 ID 与幂等键 → 新增不含学生的 `PresentItemContext`，不进适配器幂等缓存。
- 安全审查阻断：`AYA_CONTENT_PACK` 在任何环境都能换掉整份内容包（含验证器标准答案）且看不出来 → 只在候选开关打开时读取，来源版本带内容包 sha256 前缀。
- 建议：富文本排除方向控制与不可见字符；只放行 original / licensed / public 的题面（unverified 暂按无题面）；缓存加 TTL；取题面移出数据库事务；文砺停在 1.0 时按“没有题面”处理；认不出的 LaTeX 命令整题退回纯文字；BFF 两路径统一；总字数上限 8000；题干答案词扫描；知行幂等偶发失败改确定性（W13 T09）。

### 修复中的偏差
- 题干答案扫描不查单字符答案，“解析式”不算解析标记（否则合成夹具误报）。
- 学习服务端到端用的合成题面包，把对应题标为自编、授权已核、pending_review（带合成审核记录），才能通过“只放行 original / licensed / public”。
- BFF 诊断路径的选项键仍是 `^[A-Z]$`（只出阿鸦题）；再练最多选 8 项未改（契约上限 10）。
- 诊断取题面移出事务：事务内只查缓存，没命中则回滚、事务外预取后重做，最多 4 次，之后 503 `ITEM_VIEW_UNAVAILABLE`。
- web 没有变异脚本，新建 `tests/rich-item-mutations.test.ts`（12 个变异，含对照组）。
- 经验：Edit 工具会把 `\uXXXX` 转成真实字符，写正则时用十六进制码位或变通写法，并扫描确认文件里没有不可见字符。

## 待人确认
1. 契约评审重点：`content_state`；受限富文本的字符与 LaTeX 命令黑名单；`present_item` 请求按契约仍须带 `student_ref` 与 `idempotency_key`（非消耗、与学生无关）。
2. 阿鸦内容包格式 3 新增可选 `stem`、`option_content` 与 `AYA_CONTENT_PACK` 开关；真实题干何时、以什么来源进内容包（合规、教研）。
3. 版权状态 unverified 的题面能否给学生看，由谁决定。
4. 开关打开时再练的判分版本与判分方式仍取合成登记（practice-loop-stage1 待人确认 2、3）。
5. 是否引入 KaTeX（MIT）。
6. 覆盖率报告新增字段 `read_only_methods`（`present_item` 单列，不计入六个学科方法的分母）；Gate 0 统计工具读报告时需知道。
7. `present_item` 的幂等键可选、适配器不做冲突检测（只读），契约签字时确认。
8. 前端可排版的 LaTeX 命令表（web 与 BFF 各一份，对拍测试锁住）由谁维护。
9. 诊断事务外预取最多重试 4 次是否合适。
10. 停在 1.0 的学科调 `present_item` 一律当“没有题面”，目前没有单独的告警或计数。
11. 诊断路径的选项键要不要也放开到 `[A-Za-z0-9]{1,8}`（现在只认单个大写字母，阿鸦只用 A–D）。

## 经验
- 含 `…_TOKEN` 与 `tail` 的原始命令会被 guard.py 判为读取密钥而拦下；跑一致性测试一律用 `make conformance`。

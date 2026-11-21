# 再练与复测列表：知识点名称、手机卡片排列 · 实现记录

- 2026-11-16 用户“按建议，默认值都同意，开始实现”：spec 第五节三条都按默认（名称取已发布地图、不加手机视觉基线、顺带改 `check.yml` 注释）。

## 做了什么
- **BFF**：新文件 `services/bff/src/node-names.ts`：已发布地图、未停用节点的取名 SQL（`PUBLISHED_NODE_NAMES_SQL`）与两个函数。
  - `publishedNodeNames`：在已有连接上用。今日计划改用它，原来 `shell-routes.ts` 里的同一句删掉，三处同一口径。
  - `nodeNamesOrEmpty`：单独开一次 RLS 连接，任何失败返回空表，只记事件名 `node_names_unavailable`。
  - 再练、复测列表的每条加 `node_name`，节点 ID 去重后一次查完。取不到为 `null`，列表照常 200。
  - `openapi/practice.v0.yaml`、`retest.v0.yaml` 的 Entry 加 `node_name`（可空、必有）。
- **前端**：
  - `practice/view.ts` 的 `pickNodeName`（1–100 字符、不含控制字符），复测共用。编号不合口径时名称也不显示。
  - 两页“知识点”格：有名称显示名称（`data-value="node-name"`），没有名称显示编号（`data-value="node"`，与原来一样）。
  - **手机卡片排列**：`globals.css` 窄屏段新增 `.card-table`。表头视觉隐藏，行和格改成块级，每格用 `::before` 显示 `data-label`。表格各层显式 `role`。桌面不变。
- **测试**：
  - BFF：`tests/node-names.test.ts`（假连接池：有名、无名、查询出错，再练、复测各一遍，SQL 口径）；`tests/node-names.integration.test.ts`（真实库，事务内建三份地图、最后回滚：只有已发布地图里未停用的节点有名）；再练、复测集成测试的字段白名单加 `node_name`。
  - BFF 变异 5 条“节点名：”（不限已发布、不限未停用、取名失败时列表失败、再练 / 复测列表不带名称），全部被发现。
  - 前端单测：`node_name` 的白名单与回退；两页每格都带 `data-label`、表头与格子文案一致；卡片样式只在 767px 以下。
  - 端到端（practice-on）：102、103、复测 404 的节点改用合成数学地图 0.9.0 的节点，显示名称、编号不进页面，其余显示编号。360 / 390 / 430 宽下卡片排列、无横向滚动，列名与表头文案一致，axe 不退化。
  - `e2e/dom-leak.ts` 登记 `data-label`（只许这两张表的表头文案）。
- `.github/workflows/check.yml`：`cjk-path` 的耗时注释改为实测约 5 分钟（run 43820076356）。

## 与 spec 的差异
1. 有名称时**编号不再放进 DOM**：spec 写“编号仍放在 `data-value="node"`、名称另放”。实现时发现 `expectNoDomLeak` 规定大写点分编号只能在 `data-value` 的文本里，`data-*` 属性要逐个登记。把编号塞进属性就得给它开口子，所以改成有名称只放名称。测试按 `data-value="node-name"` 定位，编号只在没有名称时出现。
2. 合成数据：e2e 用的是“星图 v1”合成种子里已发布的数学地图 0.9.0。注意节点 ID 的主题段：第 1–10 号在 `T1`，11–20 号在 `T2`。第一次用了不存在的 `MATH.SV1A.T1.N011`，e2e 当场报出来，改为 `T1.N004`。

## 待人确认
1. **视觉基线**：`e2e/__screenshots__/practice.spec.ts/` 的“错题再练 列表”基线里，102、103 两行现在显示节点名而不是编号，基线会有差异。这项不在 `make web-test`、CI 里（`make web-visual` 单独跑）。本机改动前这两张基线就对不上（本机 Chromium 与锁定版本不同，像素差 2–5%），所以本机判断不了。请视觉负责人在锁定环境里 `make web-visual UPDATE=1` 更新并签字。
2. **名称的口径**：现在显示的是知识地图里的节点名（例如“一次函数的图像”），面向教研的命名未必适合学生直接看。编译层以后若提供学生可读的名称，再换来源（spec 待人确认第 1 条的另一选项）。
3. **信任界面的表格**（“为什么”的证据表、对账卡的偏差节点）仍显示编号。加名称要动洞察服务契约（`packages/contracts`，必停），改结构要按规则 11 附前后对比，未做。

# 错因跨科大类映射表 v0.9

状态：**草稿，待各科教研顾问逐条签字**（开发计划 冲刺 0 W2 验收项）。
数据源：`docs/mapping/error-taxonomy.v0.9.yaml`。本文的映射表由 `check_error_taxonomy.py --write` 从 YAML 生成，**请不要手工改表格**，改 YAML 后重新生成。
依据：开发计划 冲刺 0 W2（七大类）；完整方案 v1.0 第四章（跨科指标）、第 5.5 节（错因映射）；四科盘点（`specs/inventory-*`）。

## 这张表用来做什么

四科各有自己的错因分类：
- 文砺六类，外加阅读模块的六个大类和 15 个标签；
- 阿鸦十二类；
- 知行四类；
- 英语三维，外加发音信号。

各科**保留自己的分类**，契约里记为 `subject_category`。平台再把它们归到**七个跨科大类**，契约里记为 `cross_subject_category`，数据库里是 `platform.cross_subject_error`。这样才能回答完整方案提出的“共性根因”问题。例如，语文“审题性”、数学“审题错误”、道法“审题错”会合并成一条学习力指标，家长端一句话就能读完。

两条使用规则（完整方案第四章）：
1. 跨科指标（如“审题力”）必须有**至少两科**的明确证据，才能对学生和家长呈现。
2. 不作人格或心理诊断。心理与情绪只记录和学习相关的状态。

## 七个跨科大类

| id | 名称 | 定义 |
|---|---|---|
| `reading_the_question` | 审题 | 没读懂题干、设问类型、限定词或主体，作答方向偏离 |
| `locating_information` | 定位信息 | 没找到或找错了答题依据所在的位置（文本区间、材料、知识挂接点） |
| `thinking_method` | 思维方法 | 方法选择、推理、归纳、分类、转化等思维过程出错 |
| `computation_or_grammar` | 运算语法 | 运算步骤出错，或语言形式（语法）出错 |
| `expression_convention` | 表达规范 | 方向与内容基本正确，但表述、术语、格式、书写不规范或不完整 |
| `strategy_and_time` | 策略时间 | 时间分配、检查验算、答题策略与习惯问题 |
| `knowledge` | 知识 | 概念、知识点、词汇本身不会或记错 |

这七个 id 已写进契约 v0.9 和学习者 Schema v0.9。**改名或增删大类属于契约与迁移变更**（必停项）。

## 映射表

<!-- BEGIN GENERATED: mapping -->
| 学科 | 分组 | 学科编码 | 原名称 | 映射到 | 状态 | 备选 / 说明 |
|---|---|---|---|---|---|---|
| 语文 · 文砺 | 平台层六类（诊断、错题本使用） | `zhishi` | 知识性 | 知识 `knowledge` | ✅ 明确 |  |
| 语文 · 文砺 | 平台层六类（诊断、错题本使用） | `shenti` | 审题性 | 审题 `reading_the_question` | ✅ 明确 |  |
| 语文 · 文砺 | 平台层六类（诊断、错题本使用） | `dingwei` | 定位性 | 定位信息 `locating_information` | ✅ 明确 |  |
| 语文 · 文砺 | 平台层六类（诊断、错题本使用） | `siwei` | 思维性 | 思维方法 `thinking_method` | ✅ 明确 |  |
| 语文 · 文砺 | 平台层六类（诊断、错题本使用） | `biaoda_guifan` | 表达规范性 | 表达规范 `expression_convention` | ✅ 明确 |  |
| 语文 · 文砺 | 平台层六类（诊断、错题本使用） | `celue_shijian` | 策略时间性 | 策略时间 `strategy_and_time` | ✅ 明确 |  |
| 语文 · 文砺 | 阅读模块六个大类（下辖 15 个错因标签，标签继承所属大类的映射） | `reading_misread_prompt` | 审题偏差 | 审题 `reading_the_question` | ✅ 明确 | 下辖标签：`prompt_type_confuse`、`prompt_requirement_miss` |
| 语文 · 文砺 | 阅读模块六个大类（下辖 15 个错因标签，标签继承所属大类的映射） | `reading_wrong_location` | 定位错误 | 定位信息 `locating_information` | ✅ 明确 | 下辖标签：`location_wrong_region` |
| 语文 · 文砺 | 阅读模块六个大类（下辖 15 个错因标签，标签继承所属大类的映射） | `reading_summarize_inaccurate` | 概括不准 | 思维方法 `thinking_method` | ⚠️ 待定 | 备选：表达规范；下辖标签：`summary_copy_original`、`summary_point_missing`、`surface_meaning_only`；只摘原句、要点遗漏属于归纳能力（思维）；但判分维度记在“要点”，也可看作表达不到位 |
| 语文 · 文砺 | 阅读模块六个大类（下辖 15 个错因标签，标签继承所属大类的映射） | `reading_evidence_insufficient` | 依据不足 | 定位信息 `locating_information` | ⚠️ 待定 | 备选：思维方法；下辖标签：`evidence_label_only`；判分维度记在“依据”，与定位同维；但“标签化答案”更像没有建立文本与结论的推理 |
| 语文 · 文砺 | 阅读模块六个大类（下辖 15 个错因标签，标签继承所属大类的映射） | `reading_angle_missing` | 角度遗漏 | 思维方法 `thinking_method` | ✅ 明确 | 下辖标签：`effect_missing`、`structure_or_content_missing`、`angle_incomplete`、`material_compare_missing` |
| 语文 · 文砺 | 阅读模块六个大类（下辖 15 个错因标签，标签继承所属大类的映射） | `reading_expression_weak` | 表达空泛 | 表达规范 `expression_convention` | ⚠️ 待定 | 备选：思维方法；下辖标签：`theme_vague`、`opinion_unsupported`、`verbose_no_point`、`logic_disordered`；其中“逻辑混乱无层次”“开放题观点无支撑”更接近思维方法；是否把这两个标签单独映射，请教研决定 |
| 数学 · 阿鸦 | 十二大错误根因 | `gainian_cuowu` | 概念错误 | 知识 `knowledge` | ✅ 明确 |  |
| 数学 · 阿鸦 | 十二大错误根因 | `zhishi_queshi` | 知识缺失 | 知识 `knowledge` | ✅ 明确 |  |
| 数学 · 阿鸦 | 十二大错误根因 | `shenti_cuowu` | 审题错误 | 审题 `reading_the_question` | ✅ 明确 |  |
| 数学 · 阿鸦 | 十二大错误根因 | `biaozheng_cuowu` | 表征错误 | 思维方法 `thinking_method` | ⚠️ 待定 | 备选：定位信息；表征转换（图形、式子、文字之间）是思维过程；但若指“从图中读错信息”，更接近定位信息 |
| 数学 · 阿鸦 | 十二大错误根因 | `fangfa_cuowu` | 方法错误 | 思维方法 `thinking_method` | ✅ 明确 |  |
| 数学 · 阿鸦 | 十二大错误根因 | `tuili_cuowu` | 推理错误 | 思维方法 `thinking_method` | ✅ 明确 |  |
| 数学 · 阿鸦 | 十二大错误根因 | `yunsuan_cuowu` | 运算错误 | 运算语法 `computation_or_grammar` | ✅ 明确 |  |
| 数学 · 阿鸦 | 十二大错误根因 | `fenlei_yilou` | 分类遗漏 | 思维方法 `thinking_method` | ⚠️ 待定 | 备选：审题、策略时间；分类讨论不全是思维完整性问题；若因漏看条件导致，属审题；若因没有检验，属策略 |
| 数学 · 阿鸦 | 十二大错误根因 | `biaoda_cuowu` | 表达错误 | 表达规范 `expression_convention` | ✅ 明确 |  |
| 数学 · 阿鸦 | 十二大错误根因 | `jianyan_cuowu` | 检验错误 | 策略时间 `strategy_and_time` | ⚠️ 待定 | 备选：思维方法；没有验根、没有检验结果合理性，按“检查验算习惯”归策略；若是检验方法本身不会，属思维方法 |
| 数学 · 阿鸦 | 十二大错误根因 | `shijian_wenti` | 时间问题 | 策略时间 `strategy_and_time` | ✅ 明确 |  |
| 数学 · 阿鸦 | 十二大错误根因 | `xinli_xiguan` | 心理与习惯 | 策略时间 `strategy_and_time` | ⚠️ 待定（敏感） | 完整方案第四章红线“心理与情绪只记学习相关状态，不记诊断”。建议只保留“学习习惯”含义，改名为“学习习惯”，家长端不出现“心理”字样；是否保留这一类请教研与产品一起决定 |
| 道法 · 知行 | 采分点归因四类（M3 主观题判分） | `shenti_cuo` | 审题错 | 审题 `reading_the_question` | ✅ 明确 |  |
| 道法 · 知行 | 采分点归因四类（M3 主观题判分） | `dingwei_cuo` | 定位错 | 定位信息 `locating_information` | ⚠️ 待定 | 备选：知识；知行的“定位错”是“把设问挂到错误的教材章节或大概念”，不是从文本里找错位置。完整方案 5.5 把它与文砺定位归为一类，但语义上也可以算知识 |
| 道法 · 知行 | 采分点归因四类（M3 主观题判分） | `zhuanhua_cuo` | 转化错 | 思维方法 `thinking_method` | ⚠️ 待定 | 备选：表达规范；盘点疑点 zhixing 第 8 条。“命中材料事实但没有上升为学科术语、没有建立材料到理论的因果闭环”，核心是转化思维；但表现也可以是表述停留在叙述层 |
| 道法 · 知行 | 采分点归因四类（M3 主观题判分） | `guifan_cuo` | 规范错 | 表达规范 `expression_convention` | ✅ 明确 |  |
| 英语 · 开口成章 | 即时评估三维 + 发音信号 | `grammar` | 语法 | 运算语法 `computation_or_grammar` | ✅ 明确 |  |
| 英语 · 开口成章 | 即时评估三维 + 发音信号 | `vocabulary` | 词汇 | 知识 `knowledge` | ⚠️ 待定 | 备选：运算语法；不认识、不会用的词归知识；词形、搭配错误更接近语言形式（语法） |
| 英语 · 开口成章 | 即时评估三维 + 发音信号 | `task_fulfilment` | 达意 | 表达规范 `expression_convention` | ⚠️ 待定 | 备选：思维方法；没有完成交际任务、表达不完整。若是没想清楚要说什么，属思维方法 |
| 英语 · 开口成章 | 即时评估三维 + 发音信号 | `pronunciation` | 发音 | 表达规范 `expression_convention` | ⚠️ 待定 | 七大类里没有专门对应语音的类。阶段 0 发音维为 null，不产生错因；是否需要为语音新增第八类，或不纳入跨科指标，请教研决定 |

| 大类 | 映射到它的学科 | 编码数 |
|---|---|---|
| 审题 `reading_the_question` | 语文 · 文砺、数学 · 阿鸦、道法 · 知行 | 4 |
| 定位信息 `locating_information` | 语文 · 文砺、道法 · 知行 | 4 |
| 思维方法 `thinking_method` | 语文 · 文砺、数学 · 阿鸦、道法 · 知行 | 8 |
| 运算语法 `computation_or_grammar` | 数学 · 阿鸦、英语 · 开口成章 | 2 |
| 表达规范 `expression_convention` | 语文 · 文砺、数学 · 阿鸦、道法 · 知行、英语 · 开口成章 | 6 |
| 策略时间 `strategy_and_time` | 语文 · 文砺、数学 · 阿鸦 | 4 |
| 知识 `knowledge` | 语文 · 文砺、数学 · 阿鸦、英语 · 开口成章 | 4 |
<!-- END GENERATED: mapping -->

## 请教研重点拍板的问题

表中标 ⚠️ 的编码都有歧义。YAML 里写了备选大类和理由，下面几条影响最大：

1. **“定位”在三科里含义不同**。
   - 文砺：找错答题的文本区间。
   - 知行：把设问挂到了错误的教材章节。
   - 完整方案 5.5 把这两种归为一类。但知行的“定位错”语义上也可以算“知识”。归到哪类，决定了“定位”这个跨科指标到底在衡量什么。
2. **知行“转化错”**（盘点疑点 zhixing #8）：归思维方法还是表达规范？
3. **阿鸦“心理与习惯”**（敏感）：完整方案有红线，不作心理诊断。建议：
   - 只保留“学习习惯”的含义，改名为“学习习惯”，归到策略时间；
   - 家长端不出现“心理”字样。
   也可以决定不纳入跨科指标。
4. **英语“发音”**：七大类里没有对应语音的类。阶段 0 发音维为空，暂不产生错因。可以新增第八类，也可以不纳入跨科指标；如果新增，属于契约与迁移变更。
5. **文砺阅读“表达空泛”下的两个标签**：“逻辑混乱无层次”“开放题观点无支撑”更接近思维方法。是否单独映射，不再继承所属大类？
6. **阿鸦“分类遗漏”“检验错误”“表征错误”**：各有两种合理归法，见表中说明。

## 签字

| 学科 | 教研顾问 | 结论（通过 / 修改意见） | 日期 |
|---|---|---|---|
| 语文 · 文砺 | | | |
| 数学 · 阿鸦 | | | |
| 道法 · 知行 | | | |
| 英语 · 开口成章 | | | |

签字后：
- 把 YAML 的 `status` 改为 `signed`，版本升为 1.0.0；
- 在各科适配器的 grade 与 diagnose 里按本表填写 `cross_subject_category`。

## 校验

```bash
make taxonomy-check PYTHON=.venv/bin/python   # 核对七大类与契约、数据库一致，编码格式合法，表格与 YAML 同步
```

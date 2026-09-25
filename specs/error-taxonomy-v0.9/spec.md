# 错因跨科大类映射表 v0.9 起草

状态：草稿（2026-09-25），按负责人指示直接起草；映射内容待各科教研顾问签字。
关联：开发计划 冲刺 0 W2（“错因跨科大类映射表 v0.9：……教研顾问逐条签字”）· 完整方案 v1.0 第四章、第 5.5 节 · 契约 `CrossSubjectErrorCategory` · 数据库枚举 `platform.cross_subject_error`

## 目标
四科各自的错因分类（文砺六类加阅读模块六个大类、阿鸦十二类、知行四类、英语三维加发音）一一映射到七个跨科大类，让教研能逐条审、逐科签字，适配器按这张表填写 `cross_subject_category`。

## 范围
- 做：
  - `docs/mapping/error-taxonomy.v0.9.yaml`（唯一数据源）；
  - `docs/mapping/error-taxonomy.md`（给教研看的说明、生成的映射表、待拍板问题、签字表）；
  - `docs/mapping/check_error_taxonomy.py` 与 `make taxonomy-check`。
- 不做：
  - 不改七大类本身。改名、增删大类属于契约与迁移变更（必停项）。
  - 不改契约示例。检查发现的不一致只报警告，列在 PR 里。
  - 不代替教研判断。有歧义的编码标 `disputed`，给出备选和理由。

## 验收标准
- [x] 四科的每个错因编码都有来源（legacy 文件与行号，或 PRD 章节）。
- [x] 每个编码映射到七大类之一；有歧义的标 `disputed`，写明备选与理由。
- [x] 七大类 id 与契约、数据库枚举一致（脚本检查）。
- [x] 编码符合契约 `subject_category` 格式，学科内不重复（脚本检查）。
- [x] 每个大类至少有两科映射（完整方案第四章），不足时脚本报警告；目前七类全部满足。
- [x] 文档里的映射表由脚本从 YAML 生成，不同步时检查失败。
- [ ] 各科教研签字。

## 待人确认
见 `docs/mapping/error-taxonomy.md`“请教研重点拍板的问题”第 1–6 条。另外：
- 契约示例 `GenerateVariationRequestZhixing` 用了 `subject_error_category: locate`，映射表里知行的编码是 `dingwei_cuo`。是否在下次契约修订时一起改？（契约属于必停项，这次没有改）

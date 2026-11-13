# tools/content-register · 三科内容登记 SQL

把适配器内容包里的课程版本、能力点（能力格 / 判分维度节点）、题目登记进 `platform.curriculum_version`、`platform.skill_node`、`platform.item`
（证据表对题目有外键，不登记就写不进去）。**只输出 SQL，不连接任何数据库**（CLAUDE.md 规则 9）；开发库由 Claude Code 执行，pilot 与生产由人执行。

```bash
node tools/content-register/aya.ts > /tmp/aya-register.sql        # 在仓库根目录；wenli.ts、zhixing.ts 同理
cd adapters/aya && npm run register:sql                             # 等价写法（各科 package.json 的 register:sql）
psql -X -v ON_ERROR_STOP=1 -f /tmp/aya-register.sql                 # 目标库由执行人决定
```

- 只增不改（ON CONFLICT DO NOTHING；内容版本已发布或已撤下时不再追加）；准入最高登记为 `pending_review`，升级只经发布闸门。
- **为什么在这里**（W13 T11，`specs/tech-debt-batch3` 待人确认 #4，2026-11-09 确认）：登记是平台运维工具，不是适配器运行时；
  原来放在 `adapters/<科>/scripts/`，生成写 `platform.*` 的 SQL，与规则 1“适配器不直接读写平台表”字面冲突。脚本只读本科适配器的内容包加载代码
  （`adapters/<科>/src/content.ts`），`tests/placement.test.ts` 核对位置与引用。
- 测试：各科登记 SQL 的断言仍在 `adapters/<科>/tests/pack.test.ts`（随适配器测试跑）；位置守护在本目录 `tests/`（`make content-register-test`）。

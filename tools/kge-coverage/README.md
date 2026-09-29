# tools/kge-coverage · 覆盖矩阵报表

KGE-02（`specs/kge-02-atoms-tagging/spec.md` 设计要点 4）。只读：输入 KGE-01 的地图导入产物与 KGE-02 的打标报告，不连库。

```bash
cd tools/kge-coverage && npm ci   # 依赖与 tools/kge-inventory 相同（typescript、@types/node），无第三方运行时依赖
node --no-warnings src/main.ts --map ../../db/seeds/kge/aya-fun-0.1.0 \
  --tagging ../../db/seeds/kge/aya-items-<版本>/tagging-report.json --out report
```

输出 `report/coverage-<地图版本>.md` 与 `.csv`（同一输入逐字节相同，不含时间与本机路径）：

- 节点 × 认知层次的主考点题数（未标层次单列）、关联题数；
- 没有主考点题的节点；
- 题目节点数分布（{主考点} ∪ node_ids 去重），超过 3 个的标出（KGE :746）；
- 各层候选占比（`candidates_by_layer`）、题目标签来源层占比、A/B 级占比；
- 覆盖门槛（KGE :747 覆盖矩阵 100%）：有主考点题的节点 / 知识节点层与细目层的有效节点，低于 100% 标 **【红】**。
  **只标注，不作为通过条件**：退出码 0 = 生成成功；1 = 输入不一致或格式不对；2 = 参数不对。

矩阵行是 `level` 为 `node` / `detail` 的有效节点；领域与主题是结构层，主考点落在上面的题列入“需要核对的题”。

- 打标报告按打标器的类型读（`tools/kge-import/src/items/report-types.ts`，`format = kge-tagging-report/1`）；也接受没有 `format` 的简化格式（单测夹具）。
- `--scope all`（默认）：已导入主题里的全部打标结果，A 级与 B 级候选的主考点都算；`--scope item_tag`：只算写进 `kge.item_tag` 的题，与库上口径一致。
  未导入主题的题（`status = out_of_scope`）两种范围都不算，只计数。
- 报告没有 `cognitive_level` 字段时，按导入器写 `kge.item_tag` 的同一规则推：主考点节点只声明一个认知层次就用它，否则记“未标”。
- `--map` 可给多次（同一地图版本的多个专题种子，KGE-01 待人确认 #8），共用节点只留一份。

## 库上核对

`sql/coverage.sql` 按 `kge.item_tag` / `kge.knowledge_node` 算同样的数字，只读（READ ONLY 事务，最后回滚），供人在演练库上核对：

```bash
psql -X -v ON_ERROR_STOP=1 -v map_version_id=<地图版本 uuid> -f tools/kge-coverage/sql/coverage.sql
```

它的 `matrix` 段与报表（`--scope item_tag`）的 csv 逐字节相同。差别：库里驳回的标签不计；库里没有“无处可标”的题与 A/B 分级。

## 测试

```bash
cd tools/kge-coverage && npx --no-install tsc -p tsconfig.json && node --no-warnings --test --test-reporter=dot tests/*.test.ts
bash tools/kge-coverage/tests/coverage-db.sh   # 本机一次性 Postgres；退出码 3 = 本机没有 Postgres
```

`tests/coverage-db.sh` 在一次性库上执行全部迁移与二次函数种子（`db/seeds/kge/aya-fun-0.1.0`），写入合成题目的打标
（`tests/fixtures/db-tagging-report.json`），再核对 `coverage.sql` 的输出与报表计算逐字节一致、且只读。

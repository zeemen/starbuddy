# db · 平台数据库迁移

`db/migrations/` 下的任何文件都是必停项（CLAUDE.md）：PR 首行写 `[NEEDS-HUMAN]`，**迁移由人执行**。Claude Code 只写文件，不执行 `supabase db push` 之类的命令（守卫会拦截）。

## 角色（每个环境执行一次，由人执行）

```bash
psql -f db/bootstrap/roles.sql   # 建 starbuddy_bff / starbuddy_learning / starbuddy_authz
```

迁移 0001 会断言这些角色存在且属性安全，否则拒绝执行。迁移的 down 不删除角色。

## 约定（ADR 02）
- 只有一条迁移序列，编号递增：`NNNN_<schema>__<名称>.up.sql`，每个都有对应的 `.down.sql`。
- 每个迁移只改一个 schema。
- 只用标准 Postgres 16 能力，不引用托管专有的 schema（`auth.*`、`storage.*`）。
- 所有表都 `ENABLE` 并 `FORCE ROW LEVEL SECURITY`。任何 RLS 变更都要在 `db/tests/` 里附测试（规则 5）。

## 本地验证

```bash
make db-test       # 一次性本地 Postgres：迁移 up → down → up，再跑 RLS 与约束测试（约 3 秒）
make db-mutation   # 逐条放宽 RLS 或约束，断言测试能发现（约 40 秒）
```

- 需要本机装有 Postgres 16 服务端（`initdb`、`pg_ctl`）。没有的话打印“跳过”；设了 `CONTRACT_STRICT=1` 则直接失败。
- 实例建在临时目录，只监听 Unix socket，结束即删除，**不连接任何共享或生产库**（规则 9）。
- 测试数据全部是合成数据（规则 8）。

| 文件 | 内容 |
|---|---|
| `tests/00_helpers.sql` | 断言函数 `t.ok`、`t.throws` |
| `tests/10_fixtures.sql` | 两户家庭 + 一位未绑定的家长（合成；手机号用未分配的 +999） |
| `tests/20_rls_household.sql` | 家庭隔离、同手机号跨档案、注册 / 建档 / 绑定 / 邀请只能经函数、每张表的读隔离、家长自述可见性、禁用账号 |
| `tests/30_learning_service.sql` | 学习服务的读写边界、证据幂等、推断更正只能单向且指向同一对象 |
| `tests/40_constraints.sql` | 与契约一致的判分溯源约束 |
| `tests/mutations.sh` | 数据库变异测试 |

ERD 与授权模型：`docs/adr/erd.md`。

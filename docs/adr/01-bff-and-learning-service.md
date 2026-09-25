# ADR 01 · BFF（TypeScript）与学科服务（FastAPI）分工

- 状态：**草稿，待签字**
- 日期：2026-09-25
- 决策人：待定（建议：技术负责人）
- 关联：完整方案 v1.0 第六章“API / 业务”；开发计划 W2；ADR 02、03、04、06
- 来源：四份盘点 §1（技术栈）；wenli adapter-design §5；aya inventory §2.1

---

## 1. 背景

### 1.1 总纲的设定
完整方案第六章写的是：TypeScript BFF 负责账号、权益、支付回调；Python FastAPI 负责诊断、内容编排和学科验证；两者之间用 OpenAPI 契约。CLAUDE.md 的仓库结构也按这个划分：`services/bff` 和 `services/learning`。

### 1.2 盘点发现
四科都**没有**这种分层，全是单体：

| 学科 | 实际结构 |
|---|---|
| 文砺 | Next.js 16 Route Handlers，没有独立服务；长请求靠 `maxDuration=300` 硬撑 |
| 知行 | 寄宿在文砺 monorepo 的 `apps/daofa`，同样是 Route Handlers |
| 阿鸦 | Next.js 16 单体；学习状态以浏览器 localStorage 为准，整份 JSON 同步到 Supabase 一张表 |
| 开口成章 | React + Vite 前端，加一个零依赖的 Node `http` 代理 |

四科的学科逻辑（判分、诊断、错因）**全部是 TypeScript**。仓库里唯一的 Python 需求来自方案：阿鸦的 SymPy 符号验证。但阿鸦 decisions 已经把 SymPy 顺延到阶段 1，阶段 0 用 TS 写的验证器 v0 承担。

### 1.3 这份 ADR 要回答的问题
- 平台新建的服务怎么分？
- 四科的 TS 学科逻辑放在哪里运行？
- 谁有权直接写学习者模型？

---

## 2. 选项

### 选项 A：按总纲，TS BFF + Python 学习服务
- `services/bff`（TS）：家庭账号、权益与计量、支付回调、统一壳的后端接口。
- `services/learning`（Python FastAPI）：学习者模型、掌握估计、编排状态机、Rubric/验证器注册、复核队列。
- 四科适配器实现契约，由学习服务调用。适配器可以是 TS（包装现有代码），通过 HTTP 暴露。

**优点**
- 与总纲、CLAUDE.md 目录结构一致。
- 掌握估计（BKT）、统计、SymPy 在 Python 生态里更成熟。
- 学习者模型只有一个写入方，容易守住“唯一真相源”。

**缺点**
- 两种语言。适配器（TS）和学习服务（Python）之间多一跳网络。
- 团队要同时维护 TS 和 Python 的工具链。

### 选项 B：全部 TypeScript
- BFF 和学习服务都用 TS（例如 Node + Fastify），掌握估计也用 TS 写。

**优点**
- 和四科现有代码同一种语言，适配器可以作为库直接引入，不需要网络调用。

**缺点**
- 偏离总纲；BKT、统计、SymPy 这类能力要自己实现或跨语言调用。
- 学科代码和平台代码在同一进程里，“适配器不得直接读写平台表”（CLAUDE.md 第 1 条）更难靠架构保证，只能靠代码评审。

### 选项 C：全部 Python
四科 TS 逻辑都要重写，违背完整方案 5.1“不重写，适配接入”。**不推荐。**

---

## 3. 决定

**待签字。**

**建议采纳选项 A**，并明确以下边界：

1. **学习者模型只有 `services/learning` 能写。** BFF、适配器、前端都不能直接写 `platform.*` 的学习证据表（attempt、evidence_event、mastery_estimate、error_attribution）。适配器只返回契约结果，由学习服务落库。
2. **适配器以独立 HTTP 服务运行**，语言不限，实现 `packages/contracts` 的 OpenAPI。阶段 0 的四科适配器都用 TS 包装现有代码。
3. **四科原有路由里直接写学习者表的逻辑**（例如文砺的 diagnose、quiz/grade、essays/grade reflow、daily/*）改为经契约写入。原产品在接入平台之后，不再是学生画像的写入方。
4. **BFF 只做编排入口和权益**：它把请求交给学习服务之前，先完成计量预留（ADR 06），拿到 `usage_id`。
5. **服务间调用都带 `usage_id` 与 `idempotency_key`**，按 CLAUDE.md 第 4 条。

---

## 4. 后果

### 正面
- “唯一真相源”在架构上有保障：只有一个服务能写。
- 四科可以按各自节奏演进，只要契约不破。

### 代价
- 阶段 0 需要搭两套服务骨架和两条 CI 链路。
- 适配器多一跳网络，要给每个方法定超时和重试语义（写进契约）。
- 四科原产品在接入期间有两个写入方（原产品自己的表和平台）。过渡期内，**平台为准**，原产品的私有表降级为缓存或废弃（CLAUDE.md 第 2 条）。

---

## 5. 待人确认

1. 团队里有没有人能维护 Python 服务？如果按 ClaudeCode 执行方案的最小团队，Python 部分由 Claude Code 主写，资深工程师审查，是否可以接受？
2. 适配器以独立服务部署，还是阶段 0 先作为学习服务的 sidecar（同一个 Pod / 容器组）？后者部署简单，但语言仍然分开。
3. 统一壳前端（`apps/web`）是否允许直接调用学习服务的只读接口（比如星图），还是一律经过 BFF？草案倾向于一律经过 BFF，这样鉴权和家长授权只有一处。

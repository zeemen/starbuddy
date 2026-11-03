// 冲刺 4-D 四个方法的共同要求（specs/adapter-aya-methods-sprint4/spec.md 设计要点第 0、5 节与验收标准的通用条目）：
// 能力声明 6/6、契约示例、fail closed、幂等与逐字节重现、日志白名单、不读时钟、不连外部服务、一致性测试夹具的形状。
// 全部是合成数据（规则 8）。
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { parse } from "yaml";
import { ADAPTER_VERSION, createAdapter, type LogRecord } from "../src/adapter.ts";
import { contractPath, type ContractValidator } from "../src/contract.ts";
import { contextV1, contractV1, makeAdapter, makeAdapterV1, realPack, taxonomy, type Json } from "./helpers.ts";

const NEW_METHODS = ["plan", "teach", "generate_variation", "report"] as const;
const doc = parse(readFileSync(contractPath("1.0.0"), "utf8")) as Json;
const TIMESTAMP = new RegExp(doc.components.schemas.Timestamp.pattern);
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

/** 每个方法一条合法请求（真实内容包） */
function sample(method: (typeof NEW_METHODS)[number], ctx = contextV1()): Json {
  switch (method) {
    case "plan":
      return { context: ctx, mastery: [{ skill_node: "aya:P-LEQ-0409", state: "execute" }, { skill_node: "aya:P-INV-1123", state: "stable", next_retest_at: "2026-10-12T08:00:00+08:00" }] };
    case "teach":
      return { context: ctx, mode: "hint", skill_node: "aya:P-MUL-0812", item_ref: "aya:Q-MUL-U03-D-001", allowed_hint_level: 2, persona: { persona_id: "aya", prompt_version: "1.1.0" } };
    case "generate_variation":
      return { context: ctx, source_item_ref: "aya:Q-LEQ-U02-D-002", count: 2 };
    case "report":
      return { context: ctx, window: { from: "2026-10-05T00:00:00+08:00", to: "2026-10-12T00:00:00+08:00" }, audience: "student", evidence: [] };
  }
}
const RESPONSE_SCHEMA = { plan: "PlanResponse", teach: "TeachResponse", generate_variation: "GenerateVariationResponse", report: "ReportResponse" } as const;

// ---------- 能力声明 ----------
test("能力声明（默认 1.0.0）：六个方法都已实现、同步；teach 标 deterministic_no_model；report 不带 forecast_omitted；adapter_version 0.2.0", () => {
  const r = makeAdapterV1().handle("GET", "capabilities", undefined);
  assert.equal(r.status, 200);
  const b = r.body as Json;
  assert.deepEqual(contractV1.validate("Capabilities", b, "1.0.0"), { ok: true });
  assert.equal(b.contract_version, "1.0.0");
  assert.equal(b.adapter_version, "0.2.0");
  assert.equal(ADAPTER_VERSION, "0.2.0");
  for (const m of ["diagnose", "plan", "teach", "grade", "generate_variation", "report"]) {
    assert.equal(b.methods[m].implemented, true, m);
    assert.equal(b.methods[m].async, false, m);
  }
  assert.equal(b.methods.teach.notes_code, "deterministic_no_model");
  assert.ok(!("notes_code" in b.methods.report));
  // 默认 0.9.0：四个新方法未实现（它们只按 v1.0 实现），请求返回 501
  const v09 = makeAdapter().handle("GET", "capabilities", undefined).body as Json;
  assert.equal(v09.contract_version, "0.9.0");
  for (const m of NEW_METHODS) assert.equal(v09.methods[m].implemented, false, m);
  for (const m of NEW_METHODS) {
    const r09 = makeAdapter().handle("POST", m, {});
    assert.equal(r09.status, 501, m);
    assert.equal((r09.body as Json).code, "METHOD_NOT_IMPLEMENTED");
  }
});

// ---------- 正常：四个方法都有规则 3 的字段，通过契约 ----------
test("四个方法的响应都通过 v1.0 契约，带 evidence_ids、confidence、source_version、model_version（algo:）、human_review_required", () => {
  const a = makeAdapterV1();
  for (const m of NEW_METHODS) {
    const r = a.handle("POST", m, sample(m));
    assert.equal(r.status, 200, `${m} ${JSON.stringify(r.body).slice(0, 200)}`);
    const b = r.body as Json;
    assert.deepEqual(contractV1.validate(RESPONSE_SCHEMA[m], b, "1.0.0"), { ok: true }, m);
    for (const k of ["result", "evidence_ids", "confidence", "source_version", "model_version", "human_review_required", "semantics"]) assert.ok(k in b, `${m} 缺少 ${k}`);
    assert.match(b.model_version, /^algo:aya-[a-z-]+@0\.1\.0$/);
    assert.match(b.source_version, /^algo:aya-[a-z-]+@0\.1\.0;(graph:[a-z0-9-]+@[\d.]+;)?items:aya-pack@/);
    assert.equal(b.human_review_required, false);
    assert.ok(!("evidence" in b), `${m} 不产生证据`);
    // 产物里没有任何模型标识（不调模型，ADR 04）
    assert.doesNotMatch(JSON.stringify(b), /model:|deepseek|qwen|dashscope|gpt|claude/i);
  }
});

test("契约里的阿鸦示例请求都能按 v1.0 解析（示例题目、节点是合成的，不在内容包里）", () => {
  const a = makeAdapterV1();
  const ex = (name: string) => {
    const v = structuredClone(doc.components.examples[name].value);
    return v;
  };
  const cases: [string, string, number, string?][] = [
    ["PlanRequestAya", "plan", 200],
    ["TeachRequestAya", "teach", 404, "ITEM_NOT_FOUND"],
    ["TeachRequestAyaWithPage", "teach", 422, "INVALID_REQUEST"], // K-QF-0301 不是内容包里的节点
    ["GenerateVariationRequestAyaFamily", "generate_variation", 404, "ITEM_NOT_FOUND"],
  ];
  for (const [name, method, status, code] of cases) {
    const body = ex(name);
    const schema = { plan: "PlanRequest", teach: "TeachRequest", generate_variation: "GenerateVariationRequest" }[method] as "PlanRequest";
    assert.deepEqual(contractV1.validate(schema, body, "1.0.0"), { ok: true }, name);
    const r = a.handle("POST", method, body);
    assert.equal(r.status, status, `${name} ${JSON.stringify(r.body).slice(0, 200)}`);
    if (code) assert.equal((r.body as Json).code, code);
  }
  // 示例的 lesson 请求换成内容包里的节点后正常直出
  const page = ex("TeachRequestAyaWithPage");
  page.skill_node = "aya:P-MUL-0812";
  const r = a.handle("POST", "teach", page);
  assert.equal(r.status, 200);
  assert.deepEqual((r.body as Json).result.segments, [{ type: "text", text: page.knowledge_page.body_md }]);
});

// ---------- 上游失败：fail closed ----------
test("fail closed：响应不合契约时不发出，四个方法都返回 500 INTERNAL（不带响应内容）", () => {
  const strict: ContractValidator = {
    ...contractV1,
    validate: (schema, value, version) =>
      schema.endsWith("Response") && schema !== "Problem" ? { ok: false, errors: ["/ injected"] } : contractV1.validate(schema, value, version),
  };
  const logs: LogRecord[] = [];
  const a = createAdapter({ content: realPack, taxonomy, contract: strict, allowCandidate: true, log: (x) => logs.push(x) });
  for (const m of NEW_METHODS) {
    const r = a.handle("POST", m, sample(m));
    assert.equal(r.status, 500, m);
    assert.equal((r.body as Json).code, "INTERNAL");
    assert.deepEqual(contractV1.validate("Problem", r.body, "1.0.0"), { ok: true });
    assert.ok(!("result" in (r.body as Json)));
  }
  assert.deepEqual(logs.map((l) => [l.method, l.status, l.code]), NEW_METHODS.map((m) => [m, 500, "INTERNAL"]));
});

// ---------- 幂等 ----------
test("幂等：同 key 同请求体（换 request_id 也算同一请求）逐字节相同；同 key 换请求体 → 409 IDEMPOTENCY_CONFLICT", () => {
  const a = makeAdapterV1();
  for (const m of NEW_METHODS) {
    const body = sample(m);
    const r1 = JSON.stringify(a.handle("POST", m, body).body);
    const retry = structuredClone(body);
    retry.context.request_id = "00000000-0000-4000-8000-00000000abcd";
    assert.equal(JSON.stringify(a.handle("POST", m, retry).body), r1, m);
    assert.equal(JSON.stringify(makeAdapterV1().handle("POST", m, body).body), r1, `${m} 换进程`);
    const changed = structuredClone(body);
    if (m === "plan") changed.mastery = [];
    if (m === "teach") changed.allowed_hint_level = 1;
    if (m === "generate_variation") changed.count = 3;
    if (m === "report") changed.audience = "parent";
    const c = a.handle("POST", m, changed);
    assert.equal(c.status, 409, m);
    assert.equal((c.body as Json).code, "IDEMPOTENCY_CONFLICT");
  }
});

// ---------- 日志 ----------
test("四个方法的日志只含白名单字段；learner_context 与对话内容不进日志和响应", () => {
  const logs: LogRecord[] = [];
  const a = makeAdapterV1(realPack, logs);
  const code = "synthetic_ctx_marker_5e2f";
  const ctxEntry = [{ field_id: "outer.g1.study_preference", source_type: "self", value: code }];
  const bodies: [string, Json][] = [
    ["plan", { ...sample("plan"), learner_context: ctxEntry }],
    ["report", { ...sample("report"), learner_context: ctxEntry }],
    ["teach", { ...sample("teach"), conversation: [{ turn_id: "00000000-0000-4000-8000-0000000000c1", role: "student", content: code }] }],
    ["generate_variation", sample("generate_variation")],
    ["plan", { context: { request_id: code }, mastery: code }],
    ["teach", { ...sample("teach"), item_ref: "aya:NOT-IN-PACK" }],
  ];
  const out = bodies.map(([m, b]) => JSON.stringify(a.handle("POST", m, b).body));
  const allowed = new Set(["method", "status", "code", "request_id", "duration_ms", "error_class"]);
  assert.equal(logs.length, bodies.length);
  for (const r of logs) {
    for (const k of Object.keys(r)) assert.ok(allowed.has(k), `日志字段 ${k}`);
    assert.ok((NEW_METHODS as readonly string[]).includes(r.method));
    if (r.request_id) assert.match(r.request_id, /^[0-9a-f-]{36}$/);
  }
  assert.ok(!JSON.stringify(logs).includes(code));
  for (const s of out) assert.ok(!s.includes(code));
});

// ---------- 静态检查 ----------
const walk = (d: string): string[] =>
  readdirSync(d, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(`${d}/${e.name}`) : [`${d}/${e.name}`]));
const SRC = new URL("../src", import.meta.url).pathname;
const stripComments = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");

test("四个方法的代码不读系统时钟（不调用 Date.now / new Date() / performance.now）", () => {
  const files = walk(`${SRC}/methods`);
  assert.ok(files.length >= 5);
  for (const f of files) {
    const src = stripComments(readFileSync(f, "utf8"));
    assert.doesNotMatch(src, /Date\.now|new Date\s*\(|performance\.now|process\.hrtime/, f);
  }
});

test("适配器代码里没有指向知识服务、洞察服务、考情库或数据库的客户端与连接串", () => {
  for (const f of walk(SRC)) {
    const src = stripComments(readFileSync(f, "utf8"));
    assert.doesNotMatch(src, /\bfetch\s*\(|XMLHttpRequest|from\s+"node:(https|net|tls|dgram)"|\bhttp\.request\b|\bundici\b|\baxios\b/, f);
    assert.doesNotMatch(src, /(postgres(ql)?|mysql|redis|mongodb):\/\//i, f);
    assert.doesNotMatch(src, /https?:\/\/(?!adapter\.invalid)/, f);
    assert.doesNotMatch(src, /(KNOWLEDGE|INSIGHT|POLICY|DATABASE)_[A-Z_]*(URL|HOST|DSN)/, f);
    assert.doesNotMatch(src, /\b(kge|insight|policy)\.[a-z_]+\b/, f);
    // 只有 HTTP 层起服务端（createServer），别处不碰 node:http
    if (!f.endsWith("/server.ts")) assert.doesNotMatch(src, /from\s+"node:http"/, f);
  }
});

// ---------- 一致性测试夹具 ----------
const fixturesDir = new URL("../conformance/", import.meta.url);
const fx = parse(readFileSync(new URL("fixtures.yaml", fixturesDir), "utf8")) as Json;
const fx09 = parse(readFileSync(new URL("fixtures.v0.9.yaml", fixturesDir), "utf8")) as Json;

test("夹具：四个方法都有用例，每条都给 conflict_body；时间都带时区；出现的幂等键都是小写 UUIDv4", () => {
  for (const m of NEW_METHODS) assert.ok(fx.cases.some((c: Json) => c.method === m), m);
  const text = readFileSync(new URL("fixtures.yaml", fixturesDir), "utf8");
  for (const c of fx.cases.filter((c: Json) => (NEW_METHODS as readonly string[]).includes(c.method))) {
    assert.ok(c.conflict_body && Object.keys(c.conflict_body).length > 0, c.id);
    assert.ok(!("context" in (c.body ?? {})), `${c.id} 不应自带 context（由一致性测试生成，幂等键为 UUIDv4）`);
  }
  const stamps = text.match(/\d{4}-\d{2}-\d{2}T[0-9:.]+[^\s"',}\]]*/g) ?? [];
  assert.ok(stamps.length >= 7, String(stamps.length));
  for (const s of stamps) assert.match(s, TIMESTAMP, s);
  for (const k of text.match(/idempotency_key:\s*\S+/g) ?? []) assert.match(k.split(/:\s*/)[1]!, UUID_V4, k);
});

test("夹具：四个方法的请求体（配上 v1.0 上下文）与 conflict_body 变体都合契约，并且适配器正常应答", () => {
  const a = makeAdapterV1();
  let n = 0;
  for (const c of fx.cases.filter((c: Json) => (NEW_METHODS as readonly string[]).includes(c.method))) {
    for (const body of [c.body, { ...c.body, ...c.conflict_body }]) {
      n++;
      const ctx = contextV1({ content_version: fx.content_version, idempotency_key: `4f000000-0000-4000-8000-${String(n).padStart(12, "0")}` });
      if (c.method !== "teach") delete (ctx as Json).usage_id;
      const req = { context: ctx, ...structuredClone(body) };
      const schema = { plan: "PlanRequest", teach: "TeachRequest", generate_variation: "GenerateVariationRequest", report: "ReportRequest" }[c.method as string] as "PlanRequest";
      assert.deepEqual(contractV1.validate(schema, req, "1.0.0"), { ok: true }, c.id);
      const r = a.handle("POST", c.method, req);
      assert.equal(r.status, 200, `${c.id} ${JSON.stringify(r.body).slice(0, 200)}`);
    }
  }
});

test("夹具漂移：fixtures.v0.9.yaml 等于 fixtures.yaml 去掉四个新方法后的内容", () => {
  const { cases, ...rest } = fx;
  const { cases: cases09, ...rest09 } = fx09;
  assert.deepEqual(rest09, rest);
  assert.deepEqual(cases09, cases.filter((c: Json) => c.method === "grade" || c.method === "diagnose"));
  assert.ok(!cases09.some((c: Json) => (NEW_METHODS as readonly string[]).includes(c.method)));
});

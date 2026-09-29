// 上游失败：模型路由超时、503、拒绝（400）、令牌不对（401）、响应结构不对、没有配置。
// 适配器只经学习服务内部路由接口调用模型；错误体不透传上游原文。全部是合成数据。
import assert from "node:assert/strict";
import { test } from "node:test";
import { createAdapter, type LogRecord } from "../src/adapter.ts";
import { callRoute, MAX_RESPONSE_BYTES, parseRouteConfig, RouteError } from "../src/route-client.ts";
import { ANSWERS, contract, context, gradeBody, harness, realPack, routeConfig, taxonomy, type Json } from "./helpers.ts";
import { MARKERS, ok, startRouteStub, STUB_MODEL_VERSION } from "./route-stub.ts";

const UPSTREAM_SECRET = "上游原文-SYNTHETIC-9c1e";

async function expectProblem(r: { status: number; body: unknown }, status: number, code: string, retryable: boolean) {
  assert.equal(r.status, status, JSON.stringify(r.body));
  const b = r.body as Json;
  assert.equal(b.code, code);
  assert.equal(b.retryable, retryable);
  assert.deepEqual(contract.validate("Problem", b), { ok: true });
  assert.ok(!JSON.stringify(b).includes(UPSTREAM_SECRET), "错误体不透传上游原文");
  assert.ok(!JSON.stringify(b).includes(ANSWERS.partial01), "错误体不回显作答");
}

test("超时：504 UPSTREAM_TIMEOUT（结果未知，可以原键重试）", async () => {
  const h = await harness({ clientTimeoutMs: 150, handler: (req) => ({ ...ok(req, "{}"), delayMs: 1000 }) });
  try {
    await expectProblem(await h.adapter.handle("POST", "grade", gradeBody("synthetic-m3-01", ANSWERS.partial01)), 504, "UPSTREAM_TIMEOUT", true);
    assert.equal(h.logs.at(-1)!.route_failure, "timeout");
  } finally {
    await h.close();
  }
});

test("路由 503 ROUTE_UNAVAILABLE：503 UPSTREAM_UNAVAILABLE，retryable", async () => {
  const h = await harness();
  try {
    await expectProblem(await h.adapter.handle("POST", "grade", gradeBody("synthetic-m3-01", `${ANSWERS.partial01}${MARKERS.unavailable}`)), 503, "UPSTREAM_UNAVAILABLE", true);
  } finally {
    await h.close();
  }
});

test("路由 400 ROUTE_REJECTED、401、结构不对：502 UPSTREAM_UNAVAILABLE，不透传上游内容", async () => {
  const cases: [string, (req: Json) => { status: number; body: unknown }, boolean][] = [
    ["400", () => ({ status: 400, body: { code: "ROUTE_REJECTED", detail: UPSTREAM_SECRET } }), false],
    ["401", () => ({ status: 401, body: { code: "UNAUTHORIZED", detail: UPSTREAM_SECRET } }), false],
    ["500", () => ({ status: 500, body: { detail: UPSTREAM_SECRET } }), true],
    ["model_version 是 routed", (req) => ok(req, UPSTREAM_SECRET, { model_version: "routed" }), true],
    ["model_version 是算法", (req) => ok(req, UPSTREAM_SECRET, { model_version: "algo:x@1" }), true],
    ["source_version 不是本 Prompt", (req) => ok(req, UPSTREAM_SECRET, { source_version: "prompt:other@9.9.9" }), true],
    ["缺 model_mismatch", (req) => ({ status: 200, body: { output: UPSTREAM_SECRET, model_version: "model:synthetic-stub/x@1", source_version: `prompt:${req.prompt_template}@${req.prompt_version}` } }), true],
    ["不是 JSON", () => ({ status: 200, body: UPSTREAM_SECRET }), true],
  ];
  for (const [label, handler, retryable] of cases) {
    const h = await harness({ handler });
    try {
      await expectProblem(await h.adapter.handle("POST", "grade", gradeBody("synthetic-m3-01", ANSWERS.partial01)), 502, "UPSTREAM_UNAVAILABLE", retryable);
    } catch (e) {
      throw new Error(`${label}: ${(e as Error).message}`);
    } finally {
      await h.close();
    }
  }
});

test("令牌不对：路由桩回 401，适配器 502；网络不通：503", async () => {
  const stub = await startRouteStub((req) => ok(req, "{}"));
  try {
    const wrong = parseRouteConfig({ url: stub.url, token: "w".repeat(40) });
    assert.ok(wrong.ok);
    const a = createAdapter({ content: realPack, taxonomy, contract, route: wrong.config });
    await expectProblem(await a.handle("POST", "grade", gradeBody("synthetic-m3-01", ANSWERS.partial01)), 502, "UPSTREAM_UNAVAILABLE", false);
  } finally {
    await stub.close();
  }
  const closed = await startRouteStub((req) => ok(req, "{}"));
  const cfg = routeConfig(closed);
  await closed.close();
  const b = createAdapter({ content: realPack, taxonomy, contract, route: cfg });
  await expectProblem(await b.handle("POST", "grade", gradeBody("synthetic-m3-01", ANSWERS.partial01)), 503, "UPSTREAM_UNAVAILABLE", true);
});

test("不跟随重定向：令牌不会被转发到别的地址", async () => {
  const target = await startRouteStub((req) => ok(req, "{}"));
  const redirector = await startRouteStub(() => ({ status: 200, body: {} }));
  try {
    const { createServer } = await import("node:http");
    const srv = createServer((_req, res) => {
      res.writeHead(307, { location: `${target.url}/internal/v1/model/route` });
      res.end();
    });
    await new Promise<void>((r) => srv.listen(0, "127.0.0.1", r));
    const port = (srv.address() as { port: number }).port;
    const cfg = parseRouteConfig({ url: `http://127.0.0.1:${port}`, token: "t".repeat(40) });
    assert.ok(cfg.ok);
    const a = createAdapter({ content: realPack, taxonomy, contract, route: cfg.config });
    const r = await a.handle("POST", "grade", gradeBody("synthetic-m3-01", ANSWERS.partial01));
    assert.equal(r.status, 503);
    assert.equal(target.authHeaders.length, 0, "重定向目标没有收到请求");
    srv.close();
  } finally {
    await target.close();
    await redirector.close();
  }
});

test("启动时没有配置路由：grade 返回 503 UPSTREAM_UNAVAILABLE（不可重试），其他方法照常", async () => {
  const logs: LogRecord[] = [];
  const a = createAdapter({ content: realPack, taxonomy, contract, route: null, log: (r) => logs.push(r) });
  await expectProblem(await a.handle("POST", "grade", gradeBody("synthetic-m3-01", ANSWERS.partial01)), 503, "UPSTREAM_UNAVAILABLE", false);
  assert.equal(logs.at(-1)!.route_failure, "not_configured");
  assert.equal((await a.handle("GET", "capabilities", undefined)).status, 200);
  const d = await a.handle("POST", "diagnose", {
    context: context(),
    mode: "process",
    attempts: [{ item_ref: "zhixing:synthetic-m3-01", occurred_at: "2026-10-08T19:30:00+08:00", response: { text: ANSWERS.partial01 } }],
  });
  assert.equal(d.status, 200, JSON.stringify(d.body));
  const v = await a.handle("POST", "generate_variation", { context: context(), source_item_ref: "zhixing:synthetic-m3-01", count: 2 });
  assert.equal(v.status, 200);
});

test("路由配置：https 或本机回环；地址不得带凭据、查询串；令牌 ≥ 32 字符", () => {
  const token = "k".repeat(32);
  const good = ["https://learning.internal.invalid", "https://learning.internal.invalid/base/", "http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000"];
  for (const url of good) assert.ok(parseRouteConfig({ url, token }).ok, url);
  const r = parseRouteConfig({ url: "https://learning.internal.invalid/base/", token });
  assert.ok(r.ok && r.config.endpoint.href === "https://learning.internal.invalid/base/internal/v1/model/route");
  const badUrl = ["http://10.0.0.5:8000", "http://learning.internal.invalid", "ftp://127.0.0.1", "https://u:p@learning.internal.invalid", "https://learning.internal.invalid/?x=1", "http://127.0.0.1.evil.invalid"];
  for (const url of badUrl) assert.deepEqual(parseRouteConfig({ url, token }), { ok: false, reason: "invalid_url" }, url);
  assert.deepEqual(parseRouteConfig({ url: "https://learning.internal.invalid", token: "short" }), { ok: false, reason: "short_token" });
  assert.deepEqual(parseRouteConfig({ url: undefined, token }), { ok: false, reason: "missing" });
  assert.deepEqual(parseRouteConfig({ url: "https://learning.internal.invalid", token: undefined }), { ok: false, reason: "missing" });
});

test("失败不缓存：同一个 key 先 503、再重试成功", async () => {
  let fail = true;
  const { keywordJudge } = await import("./route-stub.ts");
  const judge = keywordJudge(realPack);
  const h = await harness({ handler: (req) => (fail ? { status: 503, body: { code: "ROUTE_UNAVAILABLE" } } : judge(req)) });
  try {
    const body = gradeBody("synthetic-m3-01", ANSWERS.partial01);
    assert.equal((await h.adapter.handle("POST", "grade", body)).status, 503);
    fail = false;
    const r = await h.adapter.handle("POST", "grade", body);
    assert.equal(r.status, 200);
    // 两次调用路由用的是同一个路由幂等键
    assert.equal(h.stub.calls.length, 2);
    assert.equal(h.stub.calls[0]!.idempotency_key, h.stub.calls[1]!.idempotency_key);
  } finally {
    await h.close();
  }
});

// ---------- 路由响应大小（审查意见第 8 条）：content-length 预检 + 流式累计，超过 256KB 立刻中止 ----------
async function rawServer(handler: (res: import("node:http").ServerResponse) => void) {
  const { createServer } = await import("node:http");
  const state = { closedEarly: false, bytesWritten: 0 };
  const srv = createServer((req, res) => {
    req.resume();
    res.on("close", () => {
      if (!res.writableFinished) state.closedEarly = true;
    });
    handler(res);
  });
  await new Promise<void>((r) => srv.listen(0, "127.0.0.1", r));
  const port = (srv.address() as { port: number }).port;
  const cfg = parseRouteConfig({ url: `http://127.0.0.1:${port}`, token: "t".repeat(40) }, { clientTimeoutMs: 5_000 });
  assert.ok(cfg.ok);
  return {
    cfg: cfg.config,
    state,
    close: () =>
      new Promise<void>((r) => {
        srv.closeAllConnections();
        srv.close(() => r());
      }),
  };
}

const routeReq = {
  task: "T2" as const,
  data_class: "L2" as const,
  subject: "zhixing" as const,
  prompt_template: "zhixing-m3-grade",
  prompt_version: "0.2.0",
  messages: [{ role: "user" as const, content: "合成" }],
  usage_id: "00000000-0000-4000-9000-000000000001",
  idempotency_key: "00000000-0000-4000-8000-000000000001",
  deadline_ms: 9000,
};

test("响应过大（content-length 预检）：不等读完，立刻中止并按上游异常处理", async () => {
  const s = await rawServer((res) => {
    res.writeHead(200, { "content-type": "application/json", "content-length": String(10 * 1024 * 1024) });
    res.write("{");
    // 不再写：如果客户端要等读完，会一直等到超时
  });
  try {
    const started = Date.now();
    await assert.rejects(callRoute(s.cfg, routeReq), (e: unknown) => e instanceof RouteError && e.failure === "bad_response");
    assert.ok(Date.now() - started < 2_000, "没有等到超时");
    await new Promise((r) => setTimeout(r, 50));
    assert.ok(s.state.closedEarly, "连接被中止");
  } finally {
    await s.close();
  }
});

test("响应过大（没有 content-length，分块流式）：累计超过 256KB 立刻中止，不把整个响应读进内存", async () => {
  let sent = 0;
  let timer: NodeJS.Timeout | undefined;
  const s = await rawServer((res) => {
    res.writeHead(200, { "content-type": "application/json" });
    const chunk = Buffer.alloc(32 * 1024, 0x61);
    timer = setInterval(() => {
      if (res.destroyed) return clearInterval(timer);
      res.write(chunk);
      sent += chunk.length;
    }, 5);
  });
  try {
    const started = Date.now();
    await assert.rejects(callRoute(s.cfg, routeReq), (e: unknown) => e instanceof RouteError && e.failure === "bad_response");
    assert.ok(Date.now() - started < 3_000, "没有等到超时");
    await new Promise((r) => setTimeout(r, 100));
    assert.ok(s.state.closedEarly, "连接被中止");
    assert.ok(sent < 4 * 1024 * 1024, `服务端被中止前只写了 ${sent} 字节`);
  } finally {
    clearInterval(timer);
    await s.close();
  }
});

test("响应过大（注入的 fetch）：读到超限就取消读取，后面的块不再拉取；状态码不是 200 也按上游异常处理", async () => {
  for (const status of [200, 503]) {
    let pulls = 0;
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      pull(c) {
        pulls++;
        c.enqueue(new Uint8Array(100 * 1024));
      },
      cancel() {
        cancelled = true;
      },
    });
    let signal: AbortSignal | undefined;
    const fetchImpl = async (_u: URL, init: RequestInit) => {
      signal = init.signal ?? undefined;
      return new Response(body, { status });
    };
    const cfg = parseRouteConfig({ url: "https://learning.internal.invalid", token: "t".repeat(40) });
    assert.ok(cfg.ok);
    await assert.rejects(callRoute(cfg.config, routeReq, fetchImpl), (e: unknown) => e instanceof RouteError && e.failure === "bad_response");
    assert.ok(pulls <= 4, `只拉了 ${pulls} 块`);
    assert.ok(cancelled, "读取被取消");
    assert.ok(signal?.aborted, "请求被中止");
  }
});

test("响应在上限以内：照常解析；经适配器时过大响应 → 502 UPSTREAM_UNAVAILABLE（结果未知，可重试）", async () => {
  const pad = "x".repeat(MAX_RESPONSE_BYTES - 1024);
  const small = await rawServer((res) => {
    const b = JSON.stringify({ output: "{}", model_version: STUB_MODEL_VERSION, source_version: "prompt:zhixing-m3-grade@0.2.0", model_mismatch: false, pad });
    res.writeHead(200, { "content-type": "application/json" });
    res.end(b);
  });
  try {
    const r = await callRoute(small.cfg, routeReq).catch((e) => e);
    // pad 是多余字段：结构校验只看约定字段，能解析说明没有被大小上限误伤
    assert.ok(!(r instanceof RouteError), String(r));
  } finally {
    await small.close();
  }
  const h = await harness({ handler: (req) => ok(req, "y".repeat(MAX_RESPONSE_BYTES + 10)) });
  try {
    await expectProblem(await h.adapter.handle("POST", "grade", gradeBody("synthetic-m3-01", ANSWERS.partial01)), 502, "UPSTREAM_UNAVAILABLE", true);
    assert.equal(h.logs.at(-1)!.route_failure, "bad_response");
  } finally {
    await h.close();
  }
});

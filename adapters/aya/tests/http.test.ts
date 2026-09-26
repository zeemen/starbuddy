// HTTP 层：服务间令牌、路径前缀、请求体大小与 JSON 解析。
import assert from "node:assert/strict";
import type { AddressInfo } from "node:net";
import { after, before, test } from "node:test";
import { createAdapterServer } from "../src/server.ts";
import { contract, gradeBody, makeAdapter, type Json } from "./helpers.ts";

const CREDENTIAL = "synthetic-service-credential-for-tests-0001";
const server = createAdapterServer(makeAdapter(), CREDENTIAL);
let base = "";
before(async () => {
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/plugin/v0`;
});
after(() => server.close());

const auth = { authorization: `Bearer ${CREDENTIAL}` };

test("没有令牌或令牌错误：401，契约 Problem", async () => {
  for (const headers of [{}, { authorization: "Bearer wrong" }, { authorization: CREDENTIAL }] as Record<string, string>[]) {
    const r = await fetch(`${base}/capabilities`, { headers });
    assert.equal(r.status, 401);
    assert.deepEqual(contract.validate("Problem", await r.json()), { ok: true });
  }
});

// 冲刺 3-B 攻击面 F2、F3：令牌变形、别的方案、混用的令牌都要 401
const LEARNING_CREDENTIAL = "synthetic-bff-to-learning-credential-0001"; // 学习服务那一侧的令牌（混用）
const BAD_AUTH: Record<string, string> = {
  空值: "",
  只有Bearer: "Bearer",
  错误令牌: "Bearer wrong",
  令牌前缀: `Bearer ${CREDENTIAL.slice(0, -1)}`,
  令牌多一个字符: `Bearer ${CREDENTIAL}x`,
  令牌前面多字符: `Bearer x${CREDENTIAL}`,
  令牌转大写: `Bearer ${CREDENTIAL.toUpperCase()}`,
  方案小写: `bearer ${CREDENTIAL}`,
  方案大写: `BEARER ${CREDENTIAL}`,
  Basic方案: `Basic ${CREDENTIAL}`,
  Bearer后两个空格: `Bearer  ${CREDENTIAL}`,
  Bearer后制表符: `Bearer\t${CREDENTIAL}`,
  令牌重复两次: `Bearer ${CREDENTIAL}${CREDENTIAL}`,
  两个令牌用逗号拼接: `Bearer ${CREDENTIAL}, Bearer ${CREDENTIAL}`,
  较长的头8KiB: "Bearer " + "A".repeat(8 * 1024),
  学习服务令牌打适配器: `Bearer ${LEARNING_CREDENTIAL}`,
};

test("令牌变形、别的方案、学习服务令牌混用：GET 与 POST 都是 401，响应不回显令牌", async () => {
  assert.notEqual(LEARNING_CREDENTIAL, CREDENTIAL);
  for (const [label, value] of Object.entries(BAD_AUTH)) {
    for (const [method, path] of [["GET", "capabilities"], ["POST", "grade"], ["POST", "diagnose"]] as const) {
      const r = await fetch(`${base}/${path}`, {
        method,
        headers: { authorization: value, "content-type": "application/json" },
        body: method === "POST" ? JSON.stringify(gradeBody("Q-EQU-U03-D-001", { text: "9,-5" })) : undefined,
      });
      const text = await r.text();
      assert.equal(r.status, 401, `${label} ${method} ${path}`);
      assert.deepEqual(contract.validate("Problem", JSON.parse(text)), { ok: true });
      assert.ok(!text.includes(CREDENTIAL) && !text.includes(LEARNING_CREDENTIAL), label);
    }
  }
});

test("超长 authorization 头（64 KiB）：在鉴权之前就被 HTTP 层拒绝（431）", async () => {
  const r = await fetch(`${base}/capabilities`, { headers: { authorization: "Bearer " + "A".repeat(64 * 1024) } });
  assert.equal(r.status, 431);
});

test("鉴权失败时不读请求体：只发请求头就立即得到 401；令牌正确时才会等请求体（对照组）", async () => {
  const net = await import("node:net");
  const port = (server.address() as AddressInfo).port;
  // 声明 1000 字节请求体但一个字节也不发，等 300 毫秒看服务有没有回应
  const probe = (authorization: string) =>
    new Promise<string>((resolve) => {
      let got = "";
      const s = net.connect(port, "127.0.0.1", () => {
        s.write(`POST /plugin/v0/grade HTTP/1.1\r\nHost: x\r\nAuthorization: ${authorization}\r\nContent-Type: application/json\r\nContent-Length: 1000\r\n\r\n`);
      });
      s.on("data", (d) => (got += d));
      s.on("error", () => resolve(got));
      setTimeout(() => {
        s.destroy();
        resolve(got);
      }, 300);
    });
  for (const value of ["Bearer wrong", `Bearer ${LEARNING_CREDENTIAL}`, `bearer ${CREDENTIAL}`]) {
    assert.match(await probe(value), /^HTTP\/1\.1 401/, value);
  }
  assert.equal(await probe(`Bearer ${CREDENTIAL}`), "", "对照组：令牌正确时服务在等请求体，不应已回应");
});

test("令牌过短时拒绝创建服务", () => {
  assert.throws(() => createAdapterServer(makeAdapter(), "short"));
});

test("GET capabilities 与 POST grade 走通", async () => {
  const c = await fetch(`${base}/capabilities`, { headers: auth });
  assert.equal(c.status, 200);
  const g = await fetch(`${base}/grade`, {
    method: "POST",
    headers: { ...auth, "content-type": "application/json" },
    body: JSON.stringify(gradeBody("Q-EQU-U03-D-001", { text: "9,-5" })),
  });
  assert.equal(g.status, 200);
  const body = (await g.json()) as Json;
  assert.equal(body.result.verdict, "correct");
  assert.deepEqual(contract.validate("GradeResponse", body), { ok: true });
});

test("非法 JSON 400、请求体过大 413、未实现方法 501、前缀之外 404", async () => {
  const post = (path: string, body: string) => fetch(`${base}/${path}`, { method: "POST", headers: auth, body });
  assert.equal((await post("grade", "{not json")).status, 400);
  assert.equal((await post("grade", "x".repeat(1024 * 1024 + 1))).status, 413);
  assert.equal((await post("teach", "{}")).status, 501);
  assert.equal((await fetch(base.replace("/plugin/v0", "/grade"), { headers: auth })).status, 404);
});

test("畸形请求目标与中途断开的请求体都不会让进程崩溃", async () => {
  const net = await import("node:net");
  const port = (server.address() as AddressInfo).port;
  const raw = (payload: string, end = true) =>
    new Promise<string>((resolve) => {
      const s = net.connect(port, "127.0.0.1", () => {
        s.write(payload);
        if (!end) setTimeout(() => s.destroy(), 50);
      });
      let got = "";
      s.on("data", (d) => (got += d));
      s.on("close", () => resolve(got));
      s.on("error", () => resolve(got));
    });
  const bad = await raw("GET http://[ HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n");
  assert.match(bad, /^HTTP\/1\.1 400/);
  await raw(`POST /plugin/v0/grade HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer ${CREDENTIAL}\r\nContent-Length: 1000\r\n\r\n{"cont`, false);
  // 服务仍然可用
  const r = await fetch(`${base}/capabilities`, { headers: auth });
  assert.equal(r.status, 200);
});

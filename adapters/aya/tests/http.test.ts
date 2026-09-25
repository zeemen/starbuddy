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

// 启动前的安全检查（审查意见第 7、9 条）：路由调用不得走代理；两个服务间令牌必须不同。
// 拒绝启动时退出码非 0，错误信息不含地址、令牌与代理配置。全部是合成值。
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { envProxyEnabled, startupRefusal } from "../src/startup.ts";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const SERVICE = "synthetic-service-credential-0123456789abcdef";
const ROUTE = "synthetic-route-credential-fedcba9876543210xyz";
const ROUTE_URL = "https://route-host-synthetic.invalid";
const PROXY = "http://proxy-host-synthetic.invalid:3128";

test("envProxyEnabled：NODE_USE_ENV_PROXY 任何非空值、命令行或 NODE_OPTIONS 里的 --use-env-proxy 都算", () => {
  assert.equal(envProxyEnabled({}, []), false);
  assert.equal(envProxyEnabled({ NODE_USE_ENV_PROXY: "" }, []), false);
  for (const v of ["1", "0", "true", "false", "yes"]) assert.equal(envProxyEnabled({ NODE_USE_ENV_PROXY: v }, []), true, v);
  assert.equal(envProxyEnabled({}, ["--no-warnings", "--use-env-proxy"]), true);
  assert.equal(envProxyEnabled({}, ["--use-env-proxy=1"]), true);
  assert.equal(envProxyEnabled({ NODE_OPTIONS: "--max-old-space-size=512  --use-env-proxy" }, []), true);
  assert.equal(envProxyEnabled({ NODE_OPTIONS: "--max-old-space-size=512" }, ["--no-warnings"]), false);
  // 只配 HTTP(S)_PROXY 而没打开开关：Node 的 fetch 不走代理
  assert.equal(envProxyEnabled({ HTTPS_PROXY: PROXY }, []), false);
});

test("startupRefusal：代理、缺服务令牌、两个令牌相同 → 拒绝；原因里不含任何配置值", () => {
  const base = { WENLI_ADAPTER_SERVICE_TOKEN: SERVICE, WENLI_ROUTE_TOKEN: ROUTE, WENLI_ROUTE_URL: ROUTE_URL };
  assert.equal(startupRefusal(base, []), null);
  assert.equal(startupRefusal({ WENLI_ADAPTER_SERVICE_TOKEN: SERVICE }, []), null, "没配路由照常启动（grade 回 503）");
  const refused = [
    startupRefusal({ ...base, NODE_USE_ENV_PROXY: "1", HTTPS_PROXY: PROXY }, []),
    startupRefusal(base, ["--use-env-proxy"]),
    startupRefusal({ ...base, NODE_OPTIONS: "--use-env-proxy" }, []),
    startupRefusal({ WENLI_ROUTE_TOKEN: ROUTE }, []),
    startupRefusal({ ...base, WENLI_ROUTE_TOKEN: SERVICE }, []),
  ];
  for (const r of refused) {
    assert.ok(r, "应拒绝启动");
    for (const secret of [SERVICE, ROUTE, ROUTE_URL, PROXY, "route-host", "proxy-host"]) assert.ok(!r.includes(secret), `原因里出现 ${secret}`);
  }
  assert.match(refused[4]!, /相同/);
});

/** 起一次正式入口 src/main.ts：看到“listening”或 5 秒后杀掉（此时退出码为 null） */
function runMain(env: Record<string, string>, nodeArgs: string[] = []): Promise<{ code: number | null; out: string }> {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [...nodeArgs, "--no-warnings", "src/main.ts"], {
      cwd: ROOT,
      env: { PATH: process.env.PATH ?? "", PORT: "0", ...env },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let out = "";
    child.stdout.on("data", (d) => {
      out += d;
      if (out.includes('"event":"listening"')) child.kill("SIGKILL");
    });
    child.stderr.on("data", (d) => (out += d));
    const timer = setTimeout(() => child.kill("SIGKILL"), 5_000);
    child.on("exit", (code) => {
      clearTimeout(timer);
      resolve({ code, out });
    });
  });
}

const baseEnv = { WENLI_ADAPTER_SERVICE_TOKEN: SERVICE, WENLI_ROUTE_TOKEN: ROUTE, WENLI_ROUTE_URL: ROUTE_URL };

function assertRefused(r: { code: number | null; out: string }, pattern: RegExp) {
  assert.notEqual(r.code, 0);
  assert.notEqual(r.code, null, "进程应自行退出而不是一直监听");
  assert.match(r.out, /wenli-adapter: .*拒绝启动/);
  assert.match(r.out, pattern);
  assert.ok(!r.out.includes("listening"));
  for (const secret of [SERVICE, ROUTE, ROUTE_URL, PROXY, "route-host", "proxy-host"]) assert.ok(!r.out.includes(secret), `输出里出现 ${secret}`);
}

test("进程级：NODE_USE_ENV_PROXY=1 → 退出码非 0，输出不含地址与令牌", async () => {
  assertRefused(await runMain({ ...baseEnv, NODE_USE_ENV_PROXY: "1", HTTPS_PROXY: PROXY, HTTP_PROXY: PROXY }), /代理/);
});

test("进程级：命令行 --use-env-proxy 或 NODE_OPTIONS=--use-env-proxy → 拒绝启动", async () => {
  assertRefused(await runMain({ ...baseEnv, HTTPS_PROXY: PROXY }, ["--use-env-proxy"]), /代理/);
  assertRefused(await runMain({ ...baseEnv, HTTPS_PROXY: PROXY, NODE_OPTIONS: "--use-env-proxy" }), /代理/);
});

test("进程级：WENLI_ROUTE_TOKEN 与 WENLI_ADAPTER_SERVICE_TOKEN 相同 → 拒绝启动", async () => {
  assertRefused(await runMain({ ...baseEnv, WENLI_ROUTE_TOKEN: SERVICE }), /相同/);
});

test("进程级对照：配置合格时正常启动并监听（证明上面的退出来自检查本身）", async () => {
  const r = await runMain(baseEnv);
  assert.equal(r.code, null, r.out);
  assert.match(r.out, /"event":"listening"/);
});

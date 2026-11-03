// 隔离：提取 legacy 数据的子进程不能写文件、不能起子进程、看不到运行者的环境变量（安全审查）
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { test } from "node:test";
import { isolatedArgs } from "../src/main.ts";

const FIX = new URL("./fixtures/", import.meta.url).pathname;

test("隔离子进程：写文件与起子进程被拒，环境变量为空", () => {
  const out = execFileSync(process.execPath, isolatedArgs(`${FIX}aya`, `${FIX}wenli`, `${FIX}escape-probe.ts`), {
    env: {},
    encoding: "utf8",
  });
  assert.deepEqual(JSON.parse(out), { write: "denied", spawn: "denied", env: "empty" });
});

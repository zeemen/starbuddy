// 合成夹具：在隔离子进程里尝试越权，输出每项是否被拦下（isolation.test.ts 用）
import { execSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { tmpdir } from "node:os";

const r: Record<string, string> = {};
try {
  writeFileSync(`${tmpdir()}/kge-escape-probe.txt`, "x");
  r.write = "allowed";
} catch {
  r.write = "denied";
}
try {
  execSync("true");
  r.spawn = "allowed";
} catch {
  r.spawn = "denied";
}
r.env = Object.keys(process.env).length === 0 ? "empty" : "visible";
process.stdout.write(JSON.stringify(r));

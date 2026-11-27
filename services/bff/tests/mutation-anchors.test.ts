// 变异表的静态自检：tests/mutations.sh 只在手动触发的变异那一份 CI 里跑，源码改了而变异的原文没跟着改时，
// 平时的 PR 看不到（175c541 之后攒下了 6 条“变异本身写错”）。这里放进 bff-test（轻量 CI 每次都跑），不起库、不跑变异：
//   - 源码变异（SM）每条的原文都在当前源码里；H6 开头的要求原文唯一（mutations.sh 的 MUT_UNIQUE）；
//   - H6 变异与 EXPECT_H6（第一条失败必须是登记的断言）一一对应：改名时两边一起改。
// 变异表由 bash 自己展开（$'…' 的转义与 mutations.sh 运行时一致），原文里的 \n 表示换行，与 mutations.sh 里的 python 同一口径。
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const BFF = fileURLToPath(new URL('..', import.meta.url));
const SCRIPT = join(BFF, 'tests/mutations.sh');

/** 只取变异表的定义（SM 数组与 EXPECT_H6 关联数组），交给 bash 展开后按 NUL 分隔输出；不执行脚本的其余部分 */
function expand(): { sm: string[]; h6: string[] } {
  const src = readFileSync(SCRIPT, 'utf8');
  const block = (start: RegExp): string => {
    const lines = src.split('\n');
    const i = lines.findIndex((l) => start.test(l));
    assert.ok(i >= 0, `mutations.sh 里没有 ${start}`);
    const j = lines.findIndex((l, k) => k > i && l === ')');
    assert.ok(j > i, `${start} 没有收尾的 )`);
    return lines.slice(i, j + 1).join('\n');
  };
  const prog = `${block(/^SM=\($/)}\n${block(/^declare -A EXPECT_H6=\($/)}\n`
    + `printf '%s\\0' "\${SM[@]}"; printf '\\x01'; printf '%s\\0' "\${!EXPECT_H6[@]}"`;
  const out = execFileSync('bash', ['-c', prog], { encoding: 'utf8' });
  const [sm, h6] = out.split('\x01') as [string, string];
  const items = (s: string) => s.split('\0').filter((x) => x !== '');
  return { sm: items(sm), h6: items(h6) };
}

test('源码变异的原文都在当前源码里（H6 要求唯一）；H6 变异与 EXPECT_H6 一一对应', () => {
  const { sm, h6 } = expand();
  assert.ok(sm.length > 100, `变异表展开得太少（${sm.length} 条），解析可能出错`);
  const stale: string[] = [];
  const names = new Set<string>();
  for (const spec of sm) {
    const [name, file, from] = spec.split('\t');
    assert.ok(name && file && from !== undefined, `变异格式不对：${spec.slice(0, 80)}`);
    assert.ok(!names.has(name), `变异重名：${name}`);
    names.add(name);
    let text: string;
    try { text = readFileSync(join(BFF, file), 'utf8'); } catch { stale.push(`${name}：没有文件 ${file}`); continue; }
    const anchor = from.replaceAll('\\n', '\n');
    const n = anchor === '' ? 0 : text.split(anchor).length - 1;
    if (n === 0) stale.push(`${name}：原文在 ${file} 里找不到`);
    else if (name.startsWith('H6：') && n !== 1) stale.push(`${name}：原文在 ${file} 里出现 ${n} 次（H6 要求唯一）`);
  }
  assert.deepEqual(stale, [], '变异原文过时：改源码时把 tests/mutations.sh 里对应的原文一起改');
  const smH6 = [...names].filter((n) => n.startsWith('H6：')).sort();
  // EXPECT_H6 也登记了库函数变异（HM，原文在迁移里，运行时由 fr() 核对），不在 SM 里
  const hm = h6.filter((n) => !names.has(n));
  assert.deepEqual(smH6.filter((n) => !h6.includes(n)), [], 'H6 源码变异没有在 EXPECT_H6 登记预期断言');
  assert.deepEqual(hm.filter((n) => !readFileSync(SCRIPT, 'utf8').includes(`["${n}"]="$(fr `)), [], 'EXPECT_H6 里有对不上任何变异的名字');
});

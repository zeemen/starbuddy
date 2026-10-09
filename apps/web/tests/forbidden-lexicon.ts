// 前端文案单测共用：读禁语词表 A–E 类（只读）。现行位置是 packages/personas/forbidden/（唯一真相源，冲刺 3-F 词表收拢）；
// 旧位置 evals/persona/diandian/lexicon/forbidden.yaml 只是待删的镜像，这里不再读它（W13 技术债 T08，specs/tech-debt-batch1）。
// apps/web 不引入 YAML 解析依赖：每条规则的第一行是流式映射 `- { rule_id: A-01, …, pattern: "…",`，
// 这里只取 rule_id 与双引号里的 pattern（按 JSON 字符串转义还原）；类别取文件名，并核对与 rule_id 前缀一致。
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const WEB = new URL('..', import.meta.url).pathname;
export const FORBIDDEN_DIR = join(WEB, '..', '..', 'packages', 'personas', 'forbidden');
/** 前端文案查的类别：A–E（与 guard 的 `--persona platform` 口径一致；F、G 与 platform 类不在这里查）。 */
export const CLASSES = ['A', 'B', 'C', 'D', 'E'] as const;

const RULE_LINE = /^\s*- \{ rule_id: ([A-Z]-\d+),.*?\bpattern: "((?:[^"\\]|\\.)*)"/;

export type ForbiddenRule = { id: string; cls: string; rx: RegExp };

/** 某一类文件里以 `- { rule_id:` 开头的行数（用来断言每一条都解析出来了）。 */
export function ruleLineCount(cls: string): number {
  return readFileSync(join(FORBIDDEN_DIR, `${cls}.yaml`), 'utf8').split('\n').filter((l) => /^\s*- \{ rule_id: /.test(l)).length;
}

export function loadForbidden(): ForbiddenRule[] {
  const out: ForbiddenRule[] = [];
  for (const cls of CLASSES) {
    const text = readFileSync(join(FORBIDDEN_DIR, `${cls}.yaml`), 'utf8');
    if (!new RegExp(`^class: ${cls}$`, 'm').test(text)) throw new Error(`${cls}.yaml 的 class 不是 ${cls}`);
    let parsed = 0;
    for (const line of text.split('\n')) {
      const m = RULE_LINE.exec(line);
      if (!m) continue;
      if (!m[1]!.startsWith(`${cls}-`)) throw new Error(`${cls}.yaml 里出现别的类的规则 ${m[1]}`);
      out.push({ id: m[1]!, cls, rx: new RegExp(JSON.parse(`"${m[2]!}"`) as string) });
      parsed++;
    }
    // 不区分写法地数 `rule_id:` 出现次数（块式映射、换行、去掉注释后的任意位置）：与解析条数不等说明有规则没按行解析出来
    const declared = (text.split('\n').map((l) => l.replace(/(^|\s)#.*$/, '')).join('\n').match(/\brule_id\s*:/g) ?? []).length;
    if (declared !== parsed) throw new Error(`${cls}.yaml 有 ${declared} 处 rule_id，只解析出 ${parsed} 条（写法变了，前端单测要跟着改）`);
  }
  return out;
}

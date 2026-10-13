// packages/stage 的变异测试（make stage-mutation）：把 src、tests、schema 复制到临时目录，每个变异只改一处（校验器、文字安全、公式、
// 状态机、白板折叠、组件、布局令牌、schema），在副本里跑 tests/*.test.ts，断言每个变异都有测试失败；不变异的对照组必须全部通过。
// 临时目录在系统临时目录下，结束时删除；不碰仓库文件。依赖 apps/web 已安装的 react 与 typescript（没有时退出码 3，非严格跳过）。
import { spawn } from 'node:child_process';
import { cpSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const PKG = fileURLToPath(new URL('..', import.meta.url));
const WEB = fileURLToPath(new URL('../../../apps/web', import.meta.url));
if (!existsSync(join(WEB, 'node_modules', 'react')) || !existsSync(join(WEB, 'node_modules', 'typescript'))) {
  console.log('stage-mutation: 跳过（apps/web 没有安装依赖；cd apps/web && npm ci）');
  process.exit(3);
}

type Mutation = [name: string, file: string, from: string, to: string, all?: 'all'];
const MUTATIONS: Mutation[] = [
  // 校验器
  ['去掉入口快照（检查与重建直接读原对象）', 'src/validate.ts', 'const input = snapshot(raw);', 'const input = raw;'],
  ['不可序列化的输入不拒绝', 'src/validate.ts', "if (input === UNSERIALIZABLE) return { ok: false, errors: [{ path: '$', code: 'not_serializable' }] };", ''],
  ['多余字段放过', 'src/validate.ts', 'if (!required.includes(k) && !optional.includes(k))', 'if (false)'],
  ['缺字段放过', 'src/validate.ts', 'if (!Object.hasOwn(o, k))', 'if (false)'],
  ['版本不核对', 'src/validate.ts', "if (input['dsl_version'] !== DSL_VERSION)", 'if (false)'],
  ['文字不查 HTML / 链接 / 不可见字符', 'src/validate.ts', '  const p = textProblem(v);\n  if (p)', '  const p = textProblem(v);\n  if (false && p)'],
  ['单行上限放宽 1', 'src/validate.ts', 'if (codePoints(v) > max)', 'if (codePoints(v) > max + 1)'],
  ['公式不过禁用表', 'src/validate.ts', 'else if (!LATEX_RE.test(latex))', 'else if (false)'],
  ['公式认不出的命令原样放过', 'src/validate.ts', 'const rendered = p ? null : latexToText(latex);', 'const rendered = p ? null : (latexToText(latex) ?? latex);'],
  ['文字右边不查越界', 'src/validate.ts', 'if (x < 0 || y - fs < 0 || y > height || x + Math.ceil(textWidthEm(text) * fs) > width)', 'if (x < 0 || y - fs < 0 || y > height)'],
  ['文字基线上方不留字号', 'src/validate.ts', 'y - fs < 0', 'y < 0'],
  ['线段右边不查越界', 'src/validate.ts', '[x1!, x2!].some((x) => x < 0 || x > width)', '[x1!, x2!].some((x) => x < 0)'],
  ['形状右边不查越界', 'src/validate.ts', 'x! + w! > width || ', ''],
  ['小数坐标放过', 'src/validate.ts', '!Number.isInteger(v)', '!Number.isFinite(v)'],
  ['元素 id 可重复', 'src/validate.ts', 'if (s.used.has(x))', 'if (false)'],
  ['高亮可以指向高亮', 'src/validate.ts', 's.alive.get(target) !== false', 's.alive.get(target) === undefined', 'all'],
  ['擦掉的元素仍算在白板上', 'src/validate.ts', '        s.alive.delete(x);', '        void x;'],
  ['擦掉目标后高亮仍算在白板上', 'src/validate.ts', 's.alive.delete(hid);', 'void hid;'],
  ['讲解权可以给任何字符串', 'src/validate.ts', "!(typeof role === 'string' && (s.roles as string[]).includes(role))", "!(typeof role === 'string')"],
  ['角色可以没有老师', 'src/validate.ts', "\n    || !roles.includes('teacher')", ''],
  ['步数上限放宽 1', 'src/validate.ts', 'if (steps.length > LIMITS.maxSteps)', 'if (steps.length > LIMITS.maxSteps + 1)'],
  ['不限动作合计', 'src/validate.ts', 'if (totalActions > LIMITS.maxActions)', 'if (false)'],
  ['合格结果不冻结', 'src/validate.ts', '  deepFreeze(dsl);\n', '\n'],
  ['合格结果不登记', 'src/validate.ts', '  registry.add(dsl);\n', '\n'],
  ['非普通对象当对象', 'src/validate.ts', ' && Object.getPrototypeOf(v) === Object.prototype', ''],
  ['错误数不设上限', 'src/validate.ts', 'if (this.errors.length < MAX_ERRORS)', 'if (true)'],
  // 文字安全与宽度
  ['不拒格式字符（零宽、双向控制符）', 'src/text.ts', '\\p{Cc}\\p{Cf}\\p{Zl}', '\\p{Cc}\\p{Zl}'],
  ['不拒落单代理项', 'src/text.ts', '\\p{Cs}', ''],
  ['不拒私用区', 'src/text.ts', '\\p{Co}', ''],
  ['不拒未分配码位', 'src/text.ts', '\\p{Cn}', ''],
  ['不拒字符实体', 'src/text.ts', '/<[A-Za-z!/?]|&#?[A-Za-z0-9]+;/u', '/<[A-Za-z!/?]/u'],
  ['不拒协议相对地址', 'src/text.ts', '[a-z][a-z0-9+.-]*:\\/\\/|\\/\\/|', '[a-z][a-z0-9+.-]*:\\/\\/|'],
  ['不拒 javascript: 伪协议', 'src/text.ts', '(?:javascript|data|', '(?:data|'],
  ['宽度估算偏窄', 'src/text.ts', '? 0.6 : 1', '? 0.5 : 0.5'],
  // 公式排版
  ['原型链上的名字当命令', 'src/latex.ts', 'Object.hasOwn(SYMBOL, name)', 'name in SYMBOL'],
  ['认不出的命令只留命令名', 'src/latex.ts', '      } else if (/^[A-Za-z]+$/.test(name)) {\n        return null;', '      } else if (/^[A-Za-z]+$/.test(name)) {\n        out += name;'],
  ['禁用表漏掉 \\href', 'src/latex.ts', '(?:href|url|', '(?:url|'],
  // 状态机
  ['走完不结束', 'src/player.ts', 'if (cursor >= s.total) return', 'if (cursor > s.total) return'],
  ['后退不后退', 'src/player.ts', 'return at(s, s.cursor - 1,', 'return at(s, s.cursor,'],
  ['跳转越界放过', 'src/player.ts', 'e.step > s.total)', 'e.step > s.total + 1)'],
  ['非播放中也能暂停', 'src/player.ts', "return s.status === 'playing' ? { ...s, status: 'paused' } : s;", "return { ...s, status: 'paused' };"],
  ['暂停时 tick 仍前进', 'src/player.ts', "return s.status === 'playing' ? at(s, s.cursor + 1, true) : s;", 'return at(s, s.cursor + 1, true);'],
  ['播放中 next 变成暂停', 'src/player.ts', "return at(s, s.cursor + 1, s.status === 'playing');", 'return at(s, s.cursor + 1, false);'],
  // 白板折叠
  ['擦掉元素时不连同高亮', 'src/board.ts', " && !(e.kind === 'highlight' && gone.has(e.target))", ''],
  ['高亮带不夹在画布内', 'src/board.ts', 'Math.max(0, target.x - HIGHLIGHT_PAD)', 'target.x - HIGHLIGHT_PAD'],
  ['讲解权不切换', 'src/board.ts', 'state.speaker = a.role;', 'void a;'],
  ['“最近一步”不按步重置', 'src/board.ts', '    state.latest = [];\n    for (const a', '    for (const a'],
  // 组件
  ['未校验的对象也渲染', 'src/Whiteboard.tsx', '  if (!isValidated(dsl)) return null;\n', ''],
  ['讲话者不加粗', 'src/Whiteboard.tsx', 'speaking ? SPEAKER_OUTLINE_LINES : OUTLINE_LINES', 'OUTLINE_LINES'],
  ['点点不出场也有占位框', 'src/Whiteboard.tsx', "dsl.roles.includes('buddy') ?", 'true ?'],
  ['高亮不画在底层', 'src/Whiteboard.tsx', "const ordered = [...board.elements.filter((e) => e.kind === 'highlight'), ...board.elements.filter((e) => e.kind !== 'highlight')];", 'const ordered = board.elements;'],
  // 布局令牌与上限常量
  ['布局令牌只冻结最外层', 'src/layout.ts', '    for (const x of Object.values(v)) deepFreeze(x);\n', ''],
  ['桌面白板不足 65%', 'src/layout.ts', 'whiteboard: { x: 20, y: 20, w: 960, h: 440 }', 'whiteboard: { x: 20, y: 20, w: 960, h: 400 }'],
  ['手机老师高度不是 18%', 'src/layout.ts', 'teacher: { x: 280, y: 578.4, w: 108, h: 129.6 }', 'teacher: { x: 280, y: 578.4, w: 108, h: 140 }'],
  ['单行上限常量改动而 schema 未改', 'src/dsl.ts', 'maxLineChars: 60,', 'maxLineChars: 80,'],
  // schema
  ['schema 单行上限放宽', 'schema/stage-dsl-0.1.0.schema.json', '"text": { "type": "string", "minLength": 1, "maxLength": 60 }', '"text": { "type": "string", "minLength": 1, "maxLength": 80 }'],
  ['schema id 格式放宽', 'schema/stage-dsl-0.1.0.schema.json', '"pattern": "^[a-z][a-z0-9_]{0,31}$"', '"pattern": "^[A-Za-z0-9_]{1,64}$"'],
  ['schema 动作允许任意字段', 'schema/stage-dsl-0.1.0.schema.json',
    '"required": ["type", "id", "target"],', '"required": ["type", "id", "target"], "additionalProperties": true,'],
];

function prepare(root: string, m: Mutation | null): string {
  const dir = mkdtempSync(join(root, 'm-'));
  for (const d of ['src', 'tests', 'schema']) cpSync(join(PKG, d), join(dir, d), { recursive: true });
  if (m) {
    const [name, file, from, to, all] = m;
    const p = join(dir, file);
    const src = readFileSync(p, 'utf8');
    const n = src.split(from).length - 1;
    if (n === 0 || (n > 1 && all !== 'all')) throw new Error(`变异“${name}”在 ${file} 里找到 ${n} 处（应恰好 1 处）`);
    writeFileSync(p, all === 'all' ? src.split(from).join(to) : src.replace(from, to));
  }
  return dir;
}

function runTests(dir: string): Promise<{ code: number; out: string }> {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, ['--no-warnings', '--test', '--test-reporter=dot', '--test-concurrency=1', 'tests/*.test.ts'], {
      cwd: dir, env: { ...process.env, STAGE_WEB_ROOT: WEB }, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let out = '';
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', (d) => { out += d; });
    child.on('close', (code) => resolve({ code: code ?? 1, out }));
  });
}

const root = mkdtempSync(join(tmpdir(), 'stage-mutation-'));
let failed = false;
try {
  const control = await runTests(prepare(root, null));
  if (control.code !== 0) {
    console.log(`stage-mutation: 对照组（不变异）没有全部通过，变异结果没有意义：\n${control.out.slice(-2000)}`);
    process.exitCode = 1;
  } else {
    const survivors: string[] = [];
    const queue = [...MUTATIONS];
    const worker = async () => {
      for (let m = queue.shift(); m; m = queue.shift()) {
        const { code } = await runTests(prepare(root, m));
        if (code === 0) survivors.push(`${m[0]}（${m[1]}）`);
      }
    };
    await Promise.all(Array.from({ length: 4 }, worker));
    if (survivors.length) {
      failed = true;
      console.log(`stage-mutation: ${survivors.length} 个变异没有被测试发现：\n  - ${survivors.join('\n  - ')}`);
    } else {
      console.log(`stage-mutation: 通过（对照组全过；${MUTATIONS.length} 个变异全部被测试发现）`);
    }
  }
} finally {
  rmSync(root, { recursive: true, force: true });
}
if (failed) process.exitCode = 1;

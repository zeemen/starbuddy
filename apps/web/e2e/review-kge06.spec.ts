// KGE-06 封测周修订支持（specs/kge06-weekly-revision）审核台端到端：开关打开的前端（practice-on 这一套，STARBUDDY_REVIEW_FEATURES 打开）
// 连 scripts/e2e.sh 起的审核台后端（REVIEW_CONSOLE_FEATURES 打开）。
// 合成数据在本用例开头经 psql 写进一次性测试库（道法合成地图 MORAL.KX.*：1.0.0 两名合成教研签字后发布、1.0.1 草稿加了一个节点；
// 三条低置信打标候选；合成教研 teacher.k6 带登录凭据）——与 review.spec.ts 的语文地图、teacher.t1 / t2 互不干扰。
// 流程：登录 → 工作单按置信度升序 → 接受一条（写回草稿的题目标签）→ 改标一条（接受另一节点、原候选驳回）→ 处理人记进签字表；
// 草稿审核页有预检入口 → 预检判为结构性、建议升次版本、两人签、1.0.1 版本号段不够。无障碍检查。
import { execFileSync } from 'node:child_process';
import { createHmac } from 'node:crypto';
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

const K6 = '6b06e2e0-0000-4000-8000-0000000000e1';
const K6B = '6b06e2e0-0000-4000-8000-0000000000e2';
const VP = '6b06e2e0-0000-4000-8000-0000000001a0';
const VD = '6b06e2e0-0000-4000-8000-0000000001a1';
const CV = '6b06e2e0-0000-4000-8000-0000000002a0';
const C1 = '6b06e2e0-0000-4000-8000-000000000301';
const C2 = '6b06e2e0-0000-4000-8000-000000000302';
const C3 = '6b06e2e0-0000-4000-8000-000000000303';
const SECRET = Buffer.from('6b'.repeat(20), 'hex');
const PASSWORD = 'synthetic-teacher-k6';

function psql(sql: string): string {
  return execFileSync('psql', ['-X', '-q', '-At', '-v', 'ON_ERROR_STOP=1', '-c', sql], { encoding: 'utf8' });
}

function nodes(v: string, extra: boolean): string {
  const rows = [
    `('${v}', 'MORAL.KX', 'domain', NULL, NULL, '合成领域', '{}', '{}', '{}', '{}')`,
    `('${v}', 'MORAL.KX.T', 'topic', 'MORAL.KX', NULL, '合成主题', '{}', '{}', '{}', '{}')`,
    `('${v}', 'MORAL.KX.T.A', 'node', 'MORAL.KX.T', 'kp', '合成节点甲', '{a}', '{b}', '{c}', '{understand}')`,
    `('${v}', 'MORAL.KX.T.B', 'node', 'MORAL.KX.T', 'kp', '合成节点乙', '{a}', '{b}', '{c}', '{understand}')`,
    ...(extra ? [`('${v}', 'MORAL.KX.T.C', 'node', 'MORAL.KX.T', 'kp', '合成节点丙', '{a}', '{b}', '{c}', '{understand}')`] : []),
  ];
  return `INSERT INTO kge.map_version (id, subject, standard_year, version) VALUES ('${v}', 'zhixing', 2022, '${extra ? '1.0.1' : '1.0.0'}');
    INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels) VALUES ${rows.join(', ')};
    INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to)
      SELECT '${v}', n.id, 'syn-2024', 2024, 'v', 'c', 1, 1 FROM kge.knowledge_node n WHERE n.map_version_id = '${v}' AND n.level = 'node';`;
}

function cand(id: string, item: string, node: string, conf: string): string {
  return `('${id}', '${VD}', 'zhixing', '${item}', '${node}', 'main', 'rule', ${conf}, 'syn-tagger@1', NULL, 'syn_low_conf', 'B', 'legacy')`;
}

function seed(): void {
  psql(`
    INSERT INTO platform.account (id, kind) VALUES ('${K6}', 'staff'), ('${K6B}', 'staff');
    SELECT platform.staff_enroll('${K6}', 'teacher.k6', '${PASSWORD}', '\\x${SECRET.toString('hex')}'::bytea);
    INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version)
      VALUES ('${CV}', 'synth-k6e2e', 2031, 8, 'syn-k6', 'zhixing', '0.1.0');
    INSERT INTO platform.skill_node (subject, local_id, curriculum_version_id, label_code) VALUES ('zhixing', 'SYN-K6E-01', '${CV}', 'syn-k6e-01');
    INSERT INTO platform.item (subject, item_id, curriculum_version_id, skill_local_id, admission) VALUES
      ('zhixing', 'SYN-K6E-I1', '${CV}', 'SYN-K6E-01', 'pending_review'), ('zhixing', 'SYN-K6E-I2', '${CV}', 'SYN-K6E-01', 'pending_review');
    SET ROLE starbuddy_kge;
    ${nodes(VP, false)}
    RESET ROLE;
    SET ROLE starbuddy_review;
    SELECT set_config('app.account_id', '${K6}', true);
    SELECT kge.sign_review('map_version', '${VP}', 'approved', 'high', 'seed');
    SELECT set_config('app.account_id', '${K6B}', true);
    SELECT kge.sign_review('map_version', '${VP}', 'approved', 'high', 'seed');
    SELECT kge.publish_map_version('${VP}');
    SELECT set_config('app.account_id', '', true);
    RESET ROLE;
    SET ROLE starbuddy_kge;
    ${nodes(VD, true)}
    INSERT INTO kge.tag_candidate (id, map_version_id, subject, item_id, node_id, role, layer, confidence, tagger_version, model_version,
                                   reason_code, grade, item_source_kind) VALUES
      ${cand(C1, 'SYN-K6E-I1', 'MORAL.KX.T.A', '0.40')}, ${cand(C2, 'SYN-K6E-I1', 'MORAL.KX.T.B', '0.55')},
      ${cand(C3, 'SYN-K6E-I2', 'MORAL.KX.T.A', '0.30')};
    RESET ROLE;`);
}

/** RFC 6238 的 6 位码（测试方独立实现） */
function totp(step: number): string {
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(step));
  const h = createHmac('sha1', SECRET).update(msg).digest();
  const o = h[19]! & 15;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000).padStart(6, '0');
}

async function hydrated(page: Page): Promise<void> {
  await expect(page.locator('form.review-form:not([data-hydrated])')).toHaveCount(0);
}

async function login(page: Page): Promise<void> {
  await page.goto('/review/login');
  await hydrated(page);
  await page.getByLabel('登录名').fill('teacher.k6');
  await page.getByLabel('口令').fill(PASSWORD);
  await page.getByLabel('6 位码').fill(totp(Math.floor(Date.now() / 30_000)));
  await page.getByRole('button', { name: '登录' }).click();
  await expect(page).toHaveURL(/\/review$/);
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(r.violations.map((v) => v.id)).toEqual([]);
}

function state(id: string): string {
  return psql(`SELECT c.status || '|' || coalesce(s.account_id::text, '-') FROM kge.tag_candidate c
                 LEFT JOIN platform.tag_candidate_signoff s ON s.tag_candidate_id = c.id WHERE c.id = '${id}'`).trim();
}

test.describe.configure({ mode: 'serial' });

test('低置信工作单：按置信度升序；接受与改标后候选状态正确、处理人记下；预检判为结构性', async ({ page }) => {
  seed();
  await login(page);
  await expect(page.getByRole('link', { name: '低置信打标' })).toBeVisible();
  await page.goto('/review/tag-candidates?subject=zhixing');
  await hydrated(page);
  const rows = page.getByTestId('tag-candidates').locator('tbody tr');
  await expect(rows).toHaveCount(3);
  expect(await rows.evaluateAll((trs) => trs.map((t) => t.getAttribute('data-candidate')))).toEqual([C3, C1, C2]);
  await expect(page.getByTestId('tag-total')).toHaveText('待处理共 3 条');
  await axe(page);

  // 接受最没把握的那条（草稿版本：写回题目标签）
  const f3 = page.getByTestId(`decide-${C3}`);
  await f3.getByRole('button', { name: '接受 MORAL.KX.T.A' }).click();
  // 成功后页面重新渲染，已处理的候选从工作单里消失（这一行与它的结果区一起卸载）
  await expect(rows).toHaveCount(2);
  await expect(page.locator(`tr[data-candidate="${C3}"]`)).toHaveCount(0);
  expect(state(C3)).toBe(`accepted|${K6}`);
  expect(psql(`SELECT main_node_id || '|' || tag_source FROM kge.item_tag WHERE map_version_id = '${VD}' AND item_id = 'SYN-K6E-I2'`).trim())
    .toBe('MORAL.KX.T.A|human');

  // 改标：C1（节点甲）换成同题的 C2（节点乙）
  await page.reload();
  await hydrated(page);
  await expect(rows).toHaveCount(2);
  const f1 = page.getByTestId(`decide-${C1}`);
  await f1.getByLabel('改标为').selectOption(C2);
  await f1.getByRole('button', { name: '改标' }).click();
  await expect(page.getByTestId('tag-empty')).toBeVisible();  // 两条都已处理
  expect(state(C2)).toBe(`accepted|${K6}`);
  expect(state(C1)).toBe(`rejected|${K6}`);
  await page.reload();
  await expect(page.getByTestId('tag-empty')).toBeVisible();

  // 草稿审核页有预检入口；预检：加了节点 → 结构性、升次版本、两人签；1.0.1 只升了修订号，发布时会被拒
  await page.goto(`/review/map-versions/${VD}`);
  await page.getByTestId('preflight-link').click();
  await expect(page).toHaveURL(new RegExp(`/review/map-versions/${VD}/preflight$`));
  await expect(page.getByTestId('preflight-structural')).toHaveAttribute('data-structural', 'true');
  await expect(page.getByTestId('preflight-bump')).toHaveAttribute('data-bump', 'minor');
  await expect(page.getByTestId('preflight-bump')).toContainText('建议版本号 1.1.0');
  await expect(page.getByTestId('preflight-signers')).toHaveAttribute('data-required', '2');
  await expect(page.getByTestId('preflight-version')).toHaveAttribute('data-ok', 'false');
  await expect(page.getByTestId('preflight-version').locator('li[data-code="structural_needs_minor"]')).toHaveCount(1);
  await expect(page.getByTestId('preflight-reasons')).toContainText('MORAL.KX.T.C');
  await expect(page.getByTestId('preflight-note')).toContainText('只给建议');
  await axe(page);
});

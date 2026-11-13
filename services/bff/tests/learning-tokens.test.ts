// PLT-24（T49）：BFF → 学习服务两枚令牌。写入令牌 BFF_LEARNING_TOKEN 只给今日计划、星图（契约 v1 的 bffServiceToken）；
// 会话令牌 BFF_LEARNING_SESSION_TOKEN 只给诊断、错题再练、复测、今日编排四个客户端。
// 单元：learningTokens 的核对；静态：src/main.ts 里每个学习服务客户端拿的是哪一枚（新增客户端不登记即失败）。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { learningTokens } from '../src/http/config.ts';

const W = 'w'.repeat(40);
const S = 'q'.repeat(40);
const URL_OK = 'http://127.0.0.1:8790';

test('learningTokens：没配学习服务地址返回 null（诊断 503、今日计划“暂无安排”）', () => {
  assert.equal(learningTokens('test', {}), null);
  assert.equal(learningTokens('test', { BFF_LEARNING_TOKEN: W, BFF_LEARNING_SESSION_TOKEN: S }), null);
});

test('learningTokens：两枚都配齐、各 ≥ 32、互不相同才通过', () => {
  assert.deepEqual(learningTokens('test', { BFF_LEARNING_URL: URL_OK, BFF_LEARNING_TOKEN: W, BFF_LEARNING_SESSION_TOKEN: S }),
                   { url: URL_OK, write: W, session: S });
  for (const env of ['dev', 'test', 'staging', 'pilot', 'production'] as const) {
    const cases: [Record<string, string>, RegExp][] = [
      [{ BFF_LEARNING_URL: URL_OK, BFF_LEARNING_TOKEN: W }, /BFF_LEARNING_SESSION_TOKEN.*至少 32/],
      [{ BFF_LEARNING_URL: URL_OK, BFF_LEARNING_TOKEN: W, BFF_LEARNING_SESSION_TOKEN: 'q'.repeat(31) }, /BFF_LEARNING_SESSION_TOKEN.*至少 32/],
      [{ BFF_LEARNING_URL: URL_OK, BFF_LEARNING_SESSION_TOKEN: S }, /BFF_LEARNING_TOKEN.*至少 32/],
      [{ BFF_LEARNING_URL: URL_OK, BFF_LEARNING_TOKEN: 'w'.repeat(31), BFF_LEARNING_SESSION_TOKEN: S }, /BFF_LEARNING_TOKEN.*至少 32/],
      [{ BFF_LEARNING_URL: URL_OK, BFF_LEARNING_TOKEN: W, BFF_LEARNING_SESSION_TOKEN: W }, /必须与 BFF_LEARNING_TOKEN 不同/],
      [{ BFF_LEARNING_URL: 'http://learning.example.invalid', BFF_LEARNING_TOKEN: W, BFF_LEARNING_SESSION_TOKEN: S }, /BFF_LEARNING_URL/],
    ];
    for (const [vars, err] of cases) {
      assert.throws(() => learningTokens(env, vars), err, `${env} ${JSON.stringify(Object.keys(vars))}`);
      try { learningTokens(env, vars); } catch (e) {
        assert.ok(!(e as Error).message.includes(W) && !(e as Error).message.includes(S), '消息不带令牌取值');
      }
    }
  }
});

test('静态：src/main.ts 的学习服务客户端按能力分组拿令牌（会话类用会话令牌，计划、星图用写入令牌）', () => {
  const src = readFileSync(new URL('../src/main.ts', import.meta.url), 'utf8');
  const expected: Record<string, 'learningSession' | 'learningCredential'> = {
    httpDiagnosisLearning: 'learningSession',
    httpPracticeLearning: 'learningSession',
    httpRetestLearning: 'learningSession',
    httpTodayOrchClient: 'learningSession',
    httpPlanClient: 'learningCredential',
    httpStarMapClient: 'learningCredential',
  };
  const calls = [...src.matchAll(/(http\w+)\(\{ baseUrl: learningUrl, token: (\w+)/g)].map((m) => [m[1]!, m[2]!] as const);
  assert.deepEqual(calls.map(([c]) => c).sort(), Object.keys(expected).sort(), '学习服务客户端清单变了：在这里登记它拿哪一枚令牌');
  for (const [client, token] of calls) assert.equal(token, expected[client], client);
  assert.match(src, /learning = learningTokens\(shellDeps\.env\)/, '启动时必须经 learningTokens 核对');
  assert.match(src, /insightCredential === learning\?\.write \|\| insightCredential === learning\?\.session/, '洞察服务令牌与两枚学习服务令牌都不同');
});

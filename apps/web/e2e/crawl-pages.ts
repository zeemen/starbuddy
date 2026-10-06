// 安全头爬取的页面登记（4-H H6，第二轮 7.6：爬遍壳内全部路由）。纯数据，不引用 Playwright：
// tests/headers-crawl.test.ts 核对 src/app 下的每个 page.tsx / route.ts 都在这里登记（新增页面不登记就失败），
// e2e/headers.spec.ts（开关全关的一套）与 e2e/trust-on.spec.ts（开关打开的一套）按它逐页核对 CSP、no-store 等安全头。
// 键 = src/app 下的文件路径；who = 用谁的会话打开（anon / student / guardian），trustOn = 只在开关打开的一套里有内容
// （开关全关的一套里同一地址是 404 页，安全头照样核对）；excluded = 不在壳的爬取里，写明理由与别处的覆盖。

export const S1 = '5a000000-0000-4000-8000-0000000000c1';  // 开关全关的一套：合成学生 S1（家长 gS）
export const GT = '5d000000-0000-4000-8000-0000000000c1';  // 开关打开的一套：合成学生 GT（家长 gH）
const NO_ID = '00000000-0000-4000-8000-0000000000f0';      // 不存在的判断 / 考试编号（页面显示“暂时无法获取”或 404）

/**
 * final：重定向后的最终地址（缺省 = path）；status：最终响应的状态码（缺省 200）。trustOn 的页面在开关全关的一套里一律 404、不重定向。
 */
export interface CrawlPage {
  who: 'anon' | 'student' | 'guardian'; path: (student: string) => string; trustOn?: true; final?: (student: string) => string; status?: number;
  /** 错题再练（specs/practice-loop-stage1 PR 4）：只在 practice 开关打开的一套（practice-on）里有内容，其余各套一律 404 页 */
  practiceOn?: true;
}
export interface Excluded { excluded: string }

export const CRAWL: Record<string, CrawlPage | Excluded> = {
  'login/page.tsx': { who: 'anon', path: () => '/login' },
  'student/page.tsx': { who: 'student', path: () => '/student', final: (s) => `/s/${s}/today` },
  'onboarding/[studentId]/page.tsx': { who: 'student', path: (s) => `/onboarding/${s}` },
  's/[studentId]/today/page.tsx': { who: 'student', path: (s) => `/s/${s}/today` },
  's/[studentId]/learn/page.tsx': { who: 'student', path: (s) => `/s/${s}/learn` },
  's/[studentId]/learn/[subject]/page.tsx': { who: 'student', path: (s) => `/s/${s}/learn/math` },
  's/[studentId]/star-map/page.tsx': { who: 'student', path: (s) => `/s/${s}/star-map` },
  's/[studentId]/learn/[subject]/practice/page.tsx': { who: 'student', path: (s) => `/s/${s}/learn/math/practice`, practiceOn: true },
  's/[studentId]/learn/[subject]/practice/[entryId]/page.tsx': { who: 'student', path: (s) => `/s/${s}/learn/math/practice/${NO_ID}`, practiceOn: true },
  's/[studentId]/why/[claimId]/page.tsx': { who: 'student', path: (s) => `/s/${s}/why/${NO_ID}`, trustOn: true },
  's/[studentId]/disputes/page.tsx': { who: 'student', path: (s) => `/s/${s}/disputes`, trustOn: true },
  's/[studentId]/exams/page.tsx': { who: 'student', path: (s) => `/s/${s}/exams`, trustOn: true },
  's/[studentId]/exams/[examId]/page.tsx': { who: 'student', path: (s) => `/s/${s}/exams/${NO_ID}`, trustOn: true },
  's/[studentId]/weekly/page.tsx': { who: 'student', path: (s) => `/s/${s}/weekly`, trustOn: true },
  'family/page.tsx': { who: 'guardian', path: () => '/family' },
  'family/[studentId]/page.tsx': { who: 'guardian', path: (s) => `/family/${s}` },
  'family/[studentId]/star-map/page.tsx': { who: 'guardian', path: (s) => `/family/${s}/star-map` },
  'family/[studentId]/time/page.tsx': { who: 'guardian', path: (s) => `/family/${s}/time` },
  'family/[studentId]/why/[claimId]/page.tsx': { who: 'guardian', path: (s) => `/family/${s}/why/${NO_ID}`, trustOn: true },
  'family/[studentId]/disputes/page.tsx': { who: 'guardian', path: (s) => `/family/${s}/disputes`, trustOn: true },
  'family/[studentId]/exams/page.tsx': { who: 'guardian', path: (s) => `/family/${s}/exams`, trustOn: true },
  'family/[studentId]/exams/[examId]/page.tsx': { who: 'guardian', path: (s) => `/family/${s}/exams/${NO_ID}`, trustOn: true },
  'family/[studentId]/weekly/page.tsx': { who: 'guardian', path: (s) => `/family/${s}/weekly`, trustOn: true },
  'family/[studentId]/data/page.tsx': { who: 'guardian', path: (s) => `/family/${s}/data`, trustOn: true },
  'family/[studentId]/data/download/route.ts': { excluded: '只认 POST 的下载路由：no-store、附件、nosniff 由 e2e/trust-on.spec.ts 的数据权利用例与 tests/trust.test.ts 核对' },
  'review/page.tsx': { excluded: '审核台（staff 登录，迁移 0020，不在壳内）：与壳同一个 proxy（CSP）与 next.config（安全头），但需要 staff 会话，本爬取不覆盖；e2e/review.spec.ts 只核对 no-store（CSP 逐页爬取登记为 lessons H6 待人确认）' },
  'review/login/page.tsx': { who: 'anon', path: () => '/review/login' },
  'review/queue/page.tsx': { excluded: '审核台（staff 登录，迁移 0020，不在壳内）：与壳同一个 proxy（CSP）与 next.config（安全头），但需要 staff 会话，本爬取不覆盖；e2e/review.spec.ts 只核对 no-store（CSP 逐页爬取登记为 lessons H6 待人确认）' },
  'review/queue/[id]/page.tsx': { excluded: '审核台（staff 登录，迁移 0020，不在壳内）：与壳同一个 proxy（CSP）与 next.config（安全头），但需要 staff 会话，本爬取不覆盖；e2e/review.spec.ts 只核对 no-store（CSP 逐页爬取登记为 lessons H6 待人确认）' },
  'review/pages/page.tsx': { excluded: '审核台（staff 登录，迁移 0020，不在壳内）：与壳同一个 proxy（CSP）与 next.config（安全头），但需要 staff 会话，本爬取不覆盖；e2e/review.spec.ts 只核对 no-store（CSP 逐页爬取登记为 lessons H6 待人确认）' },
  'review/records/page.tsx': { excluded: '审核台（staff 登录，迁移 0020，不在壳内）：与壳同一个 proxy（CSP）与 next.config（安全头），但需要 staff 会话，本爬取不覆盖；e2e/review.spec.ts 只核对 no-store（CSP 逐页爬取登记为 lessons H6 待人确认）' },
  'review/map-versions/[id]/page.tsx': { excluded: '审核台（staff 登录，迁移 0020，不在壳内）：与壳同一个 proxy（CSP）与 next.config（安全头），但需要 staff 会话，本爬取不覆盖；e2e/review.spec.ts 只核对 no-store（CSP 逐页爬取登记为 lessons H6 待人确认）' },
  // 冲刺 4 · G 流第二部分 PR D 新增的审核台页面：同上，staff 会话，本爬取不覆盖
  'review/content/page.tsx': { excluded: '审核台（staff 登录，迁移 0020，不在壳内）：与壳同一个 proxy（CSP）与 next.config（安全头），但需要 staff 会话，本爬取不覆盖（PR D 新增；CSP 逐页爬取登记为 lessons H6 待人确认）' },
  'review/content/[id]/page.tsx': { excluded: '审核台（staff 登录，迁移 0020，不在壳内）：与壳同一个 proxy（CSP）与 next.config（安全头），但需要 staff 会话，本爬取不覆盖（PR D 新增；CSP 逐页爬取登记为 lessons H6 待人确认）' },
  'review/policy/page.tsx': { excluded: '审核台（staff 登录，迁移 0020，不在壳内）：与壳同一个 proxy（CSP）与 next.config（安全头），但需要 staff 会话，本爬取不覆盖（PR D 新增；CSP 逐页爬取登记为 lessons H6 待人确认）' },
  'review/policy/[id]/page.tsx': { excluded: '审核台（staff 登录，迁移 0020，不在壳内）：与壳同一个 proxy（CSP）与 next.config（安全头），但需要 staff 会话，本爬取不覆盖（PR D 新增；CSP 逐页爬取登记为 lessons H6 待人确认）' },
  'review/spot-checks/page.tsx': { excluded: '审核台（staff 登录，迁移 0020，不在壳内）：与壳同一个 proxy（CSP）与 next.config（安全头），但需要 staff 会话，本爬取不覆盖（PR D 新增；CSP 逐页爬取登记为 lessons H6 待人确认）' },
};

/** 不对应 src/app 文件、但也要核对的地址：404 页、开发构建里的符号样张页 */
export const EXTRA_ANON: { path: string; status: number }[] = [{ path: '/no-such-page', status: 404 }, { path: '/dev/star-glyphs', status: 200 }];

/** 一次爬取的目标：地址、最终状态码、重定向后的最终路径 */
export interface CrawlTarget { path: string; status: number; final: string }

/**
 * 某个身份要爬的页面；trustOn = 当前这一套的信任界面开关是否打开（关时信任界面页面是 404 页）；
 * practiceOn = 当前这一套的错题再练开关是否打开（只有 practice-on 一套打开；关时再练页面是 404 页）
 */
export function targets(who: CrawlPage['who'], student: string, trustOn: boolean, onlyTrust = false, practiceOn = false): CrawlTarget[] {
  return Object.values(CRAWL)
    .filter((p): p is CrawlPage => 'who' in p && p.who === who && (!onlyTrust || p.trustOn === true))
    .map((p) => {
      const path = p.path(student);
      const off = (p.trustOn === true && !trustOn) || (p.practiceOn === true && !practiceOn);
      return { path, status: off ? 404 : p.status ?? 200, final: off ? path : (p.final?.(student) ?? path) };
    });
}

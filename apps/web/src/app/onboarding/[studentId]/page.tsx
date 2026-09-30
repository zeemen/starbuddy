// 建档页（SIE-01）：学生版与家长版由登录身份决定（BFF 经库判断；4-H H2 起身份只来自真实会话），每页一题，可跳过、可返回。
// 只显示当前登录人自己填过的内容（互不可见由库保证）。不缓存，不用浏览器存储，进度只存服务端。文案是占位，待教研定稿。
import { notFound, redirect } from 'next/navigation';
import { EXAM_TYPES, getOnboarding, RANK_BANDS, STEPS, SUBJECT_OPTIONS, TEXTBOOK_OPTIONS, TEXTBOOK_SUBJECTS,
  type OnboardingView, type Step } from '../../../lib/onboarding.ts';
import { pageSession } from '../../../lib/session.ts';
import { isUuid } from '../../../lib/star-map.ts';
import { startAction, stepAction } from '../actions.ts';

export const dynamic = 'force-dynamic';

function Fields({ step, view }: { step: Step; view: OnboardingView }) {
  if (step.kind === 'single' || step.kind === 'multi') {
    const current = view.reports[step.field]?.[step.prop];
    const chosen = new Set((Array.isArray(current) ? current : current === undefined ? [] : [current]).map(String));
    return (
      <fieldset>
        <legend>{step.title}</legend>
        {step.options.map((o) => (
          <p key={o.code}>
            <input type={step.kind === 'single' ? 'radio' : 'checkbox'} id={`c-${o.code}`} name="choice" value={o.code} defaultChecked={chosen.has(o.code)} />{' '}
            <label htmlFor={`c-${o.code}`}>{o.label}</label>
          </p>
        ))}
        {step.kind === 'multi' && step.note ? (
          <p>
            <label htmlFor="note">补充说明（可不填，200 字以内）</label>
            <input id="note" name="note" maxLength={200} defaultValue={String(view.reports[step.field]?.['note'] ?? '')} />
          </p>
        ) : null}
      </fieldset>
    );
  }
  if (step.kind === 'text') {
    return (
      <p>
        <label htmlFor="text">{step.title}</label>
        <input id="text" name="text" maxLength={200} defaultValue={String(view.reports[step.field]?.[step.prop] ?? '')} />
      </p>
    );
  }
  if (step.kind === 'textbooks') {
    return (
      <fieldset>
        <legend>{step.title}</legend>
        {TEXTBOOK_SUBJECTS.map((s) => {
          const cur = s.code in view.textbooks ? (view.textbooks[s.code] ?? 'unknown') : '';
          return (
            <p key={s.code}>
              <label htmlFor={`tb-${s.code}`}>{s.label}</label>{' '}
              <select id={`tb-${s.code}`} name={`textbook_${s.code}`} defaultValue={cur}>
                <option value="">（不填）</option>
                {TEXTBOOK_OPTIONS.map((o) => <option key={o.code} value={o.code}>{o.label}</option>)}
              </select>
            </p>
          );
        })}
      </fieldset>
    );
  }
  return (
    <fieldset>
      <legend>{step.title}</legend>
      <p>
        <label htmlFor="exam-subject">学科</label>{' '}
        <select id="exam-subject" name="subject" defaultValue="aya">
          {SUBJECT_OPTIONS.map((o) => <option key={o.code} value={o.code}>{o.label}</option>)}
        </select>{' '}
        <label htmlFor="exam-type">考试</label>{' '}
        <select id="exam-type" name="exam_type" defaultValue="midterm">
          {EXAM_TYPES.map((o) => <option key={o.code} value={o.code}>{o.label}</option>)}
        </select>
      </p>
      <p>
        <label htmlFor="exam-date">日期</label> <input id="exam-date" name="exam_date" type="date" />{' '}
        <label htmlFor="exam-raw">得分</label> <input id="exam-raw" name="raw_score" inputMode="decimal" size={5} />{' '}
        <label htmlFor="exam-full">满分</label> <input id="exam-full" name="full_score" inputMode="decimal" size={5} defaultValue="100" />
      </p>
      {view.role === 'guardian' ? (
        <p>
          <label htmlFor="exam-rank">大致排名（只给平台参考，不会给孩子看）</label>{' '}
          <select id="exam-rank" name="rank_band" defaultValue="unknown">
            {RANK_BANDS.map((o) => <option key={o.code} value={o.code}>{o.label}</option>)}
          </select>
        </p>
      ) : null}
    </fieldset>
  );
}

export default async function OnboardingPage({ params, searchParams }: {
  params: Promise<{ studentId: string }>;
  searchParams: Promise<{ step?: string; error?: string }>;
}) {
  const { studentId } = await params;
  // 身份只来自真实会话（4-H H2）：没有或失效去登录页；学生版、家长版由 BFF 按会话身份判断
  const ps = await pageSession();
  if (!ps) redirect('/login');
  if (!isUuid(studentId)) notFound();
  const r = await getOnboarding(ps.session, studentId);
  if (!r.ok) notFound();
  const view = r.value;
  const steps = STEPS[view.role];
  const sp = await searchParams;
  const who = view.role === 'self' ? '学生版' : '家长版';

  if (sp.step === 'done') {
    return (
      <main data-testid="onboarding" data-role={view.role}>
        <h1>建档 · {who}</h1>
        <p data-testid="done">填好了，谢谢！以后随时可以回来改。</p>
        <p><a href={`/onboarding/${studentId}?step=1`}>回去改答案</a></p>
      </main>
    );
  }
  const n = Number(sp.step);
  if (!sp.step || !Number.isInteger(n) || n < 1 || n > steps.length) {
    const started = view.progress.some((p) => p.step === 'onboarding' && p.status === 'started');
    return (
      <main data-testid="onboarding" data-role={view.role}>
        <h1>建档 · {who}</h1>
        <p>大约 5 分钟，共 {steps.length} 页。每一页都可以跳过，也可以返回修改。{view.role === 'self'
          ? '你填的答案和教材版本家长看不到，家长填的你也看不到；只有你录的考试成绩家长能看到。'
          : '孩子填的答案和教材版本您看不到，您填的孩子也看不到；孩子录的考试成绩您能看到，您录的成绩与排名孩子看不到（其他监护人能看到）。'}</p>
        <p className="muted">问题与选项是占位文案，待教研定稿。</p>
        <form action={startAction}>
          <input type="hidden" name="student" value={studentId} />
          <input type="hidden" name="csrf" value={ps.session.csrf} />
          <button type="submit">{started ? '继续填写' : '开始'}</button>
        </form>
      </main>
    );
  }
  const step = steps[n - 1]!;
  return (
    <main data-testid="onboarding" data-role={view.role} data-step={step.key}>
      <h1>建档 · {who}</h1>
      <p className="muted">第 {n} / {steps.length} 页</p>
      {sp.error === '1' ? <p role="alert" data-testid="error">这一页的填写不完整或格式不对，请再看一下，或者直接跳过。</p> : null}
      <form action={stepAction}>
        <input type="hidden" name="student" value={studentId} />
        <input type="hidden" name="csrf" value={ps.session.csrf} />
        <input type="hidden" name="step" value={n} />
        <Fields step={step} view={view} />
        <p>
          {n > 1 ? <><a href={`/onboarding/${studentId}?step=${n - 1}`}>上一步</a>{' '}</> : null}
          <button type="submit" name="op" value="skip" formNoValidate>跳过</button>{' '}
          <button type="submit" name="op" value="answer">{n === steps.length ? '完成' : '下一步'}</button>
        </p>
      </form>
    </main>
  );
}

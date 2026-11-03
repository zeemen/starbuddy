// “即将开放”的科目占位页（道法、语文在适配器与已发布内容版本都到位前；spec 第 18 节）：可以进入，也能一步回到星图。
import { SUBJECTS } from '../shell/copy.ts';

export function ComingSoon() {
  return (
    <section data-testid="coming-soon">
      <h2>{SUBJECTS.comingTitle}</h2>
      <p>{SUBJECTS.comingBody}</p>
    </section>
  );
}

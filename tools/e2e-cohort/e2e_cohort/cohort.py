"""合成队列生成器（spec 设计要点 1）：固定种子，20 名合成学生。

覆盖：
- 年级：七、八、九（写在学生档案上；三科内容包目前都只有九年级，请求里的 content_version 按各科内容包，见 lessons）；
- 选科组合：三科（数学 aya、语文 wenli、道法 zhixing），部分学生另选英语 echo（适配器已搁置，断言“英语零事件”）；
- 作答模式：全对、系统性缺口、刷同题、提示依赖、中断，各 4 名；
- 家庭：前 8 名两两同一家庭（兄弟姐妹），其余一人一户；另有一位没有任何绑定的家长（隔离断言用）。

只生成“脚本”（谁、哪科、哪道题、答什么、几点答），不碰数据库；请求体由 replay.py 按契约拼装。
所有 ID 由种子与标签派生（uuid5 → 置为 v4 形态，满足契约里幂等键的 UUIDv4 形式），同一种子逐字节相同。
作答文本全部是合成的：数学答案取自阿鸦合成/未核验内容包的标准答案与预标注陷阱，语文、道法作答由本文件的句库拼成。
"""
from __future__ import annotations

import json
import random
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

SEED = 20260930
SIZE = 20
NS = uuid.UUID("5b8e1c52-e2e0-4c0f-8a5d-000000000520")
TZ = timezone(timedelta(hours=8))
BASE_DAY = datetime(2026, 10, 12, 19, 0, tzinfo=TZ)  # 合成时间：2026-10 分区内

PATTERNS = ("all_correct", "systematic_gap", "same_item_repeat", "hint_dependent", "interrupted")
PATTERN_LABELS = {
    "all_correct": "全对", "systematic_gap": "系统性缺口", "same_item_repeat": "刷同题",
    "hint_dependent": "提示依赖", "interrupted": "中断",
}
CORE_SUBJECTS = ("aya", "wenli", "zhixing")
HEAVY_HINT = 4  # 阿鸦：用了 4 级及以上提示降权（adapters/aya adapter-design §1）

# 各科请求里的 content_version（与各科内容包 curriculum 一致，适配器按它核对）
CONTENT_VERSION = {
    "aya": {"region": "nanjing", "exam_year": 2027, "grade": 9, "textbook_version": "suke-2024", "subject": "aya", "content_version": "0.1.0"},
    "wenli": {"region": "nanjing", "exam_year": 2027, "grade": 9, "textbook_version": "bubian-2024", "subject": "wenli", "content_version": "0.1.0"},
    "zhixing": {"region": "nanjing", "exam_year": 2027, "grade": 9, "textbook_version": "bubian-2024", "subject": "zhixing", "content_version": "0.1.0"},
}
RUBRIC = {"aya": "aya-symbolic-ts@0.1.0", "wenli": "wenli-essay-5d@0.1.0", "zhixing": "zhixing-synthetic@0.1.0"}

# 数学题库（阿鸦内容包里的填空题：标准答案、一个预标注陷阱答案）。按“系统性缺口”分两组：代数式 / 几何与坐标
AYA_BANK = {
    "algebra": [("Q-ALG-U02-D-001", "3x+3", "2x+3"), ("Q-EQU-U03-D-001", "9,-5", "9"), ("Q-APP-U04-D-001", "1/4", "1")],
    "geometry": [("Q-AXS-U02-D-001", "3", "6"), ("Q-CIR-U04-D-002", "108", "72"), ("Q-CRD-U01-D-002", "(5,6)", "(6,5)")],
}
# 语文客观题（文砺合成内容包）：标准答案、一个错误答案
WENLI_OBJECTIVE = [
    ("synthetic-q01", {"choice": ["B"]}, {"choice": ["C"]}),
    ("synthetic-q02", {"choice": ["B"]}, {"choice": ["D"]}),
    ("synthetic-q04", {"text": "不亦说乎"}, {"text": "不亦乐乎"}),
    ("synthetic-q05", {"choice": ["A"]}, {"choice": ["B"]}),
    ("synthetic-q07", {"choice": ["A"]}, {"choice": ["C"]}),
    ("synthetic-q09", {"text": "吴承恩"}, {"text": "施耐庵"}),
]
ESSAYS = ("synthetic-essay-01", "synthetic-essay-02")
# 作文句库（合成）：四个锚点片段在每篇里都出现，假模型据此给出证据位置
ESSAY_ANCHORS = {"shenti_liyi": "读懂了坚持", "neirong_cailiao": "一圈一圈地跑", "jiegou_cengci": "起初我想放弃", "yuyan_biaoda": "慢慢来，比较快"}
ESSAY_OPENINGS = ("那一次，我读懂了坚持。", "那个清晨，我终于读懂了坚持。", "直到那一次，我才读懂了坚持。")
ESSAY_MIDDLES = (
    "操场上，我一圈一圈地跑，汗水模糊了视线。起初我想放弃，后来想起老师的话：慢慢来，比较快。",
    "天刚亮，我在操场一圈一圈地跑，腿像灌了铅。起初我想放弃，可耳边响起一句话：慢慢来，比较快。",
)
ESSAY_ENDINGS = ("于是我咬牙跑完了最后一圈。", "我放慢呼吸，跑完了最后一圈。", "那一刻我明白了，坚持是累了还往前走。")
# 道法主观题（知行合成内容包 synthetic-m3-01…04）：按采分点给出“学科术语 / 可接受表述 / 口语”三种写法
ZHIXING_ITEMS = ("synthetic-m3-01", "synthetic-m3-02", "synthetic-m3-03", "synthetic-m3-04")


def sid(*parts: object) -> str:
    """由标签派生的确定性 ID，UUIDv4 形态（契约里幂等键要求 v4 形式）。"""
    raw = uuid.uuid5(NS, "|".join(str(p) for p in parts))
    return str(uuid.UUID(bytes=raw.bytes, version=4))


@dataclass(frozen=True)
class Attempt:
    item: str                 # 学科内的题目本地 ID
    response: dict
    at: str                   # RFC 3339（+08:00）
    hint_level_used: int = 0


@dataclass(frozen=True)
class Step:
    """一次经契约的调用：diagnose（一组作答）或 grade（一道作答）。"""
    step_id: str              # 学生内唯一，写进幂等键派生
    method: str               # diagnose | grade
    subject: str
    attempts: tuple[Attempt, ...]
    mode: str | None = None   # diagnose：intake


@dataclass(frozen=True)
class Student:
    label: str                # S01…S20
    student_id: str
    account_id: str
    household_id: str
    guardian_id: str
    grade: int
    birth_year_month: str
    pattern: str
    enrollment: tuple[str, ...]
    steps: tuple[Step, ...]
    # 复测时这名学生怎么答（按科）：复测“做什么、什么时候”由调度决定（KGE-04 第二步：retest_task），“答什么”由队列脚本决定
    retest_answers: dict = field(default_factory=dict)
    interrupted_after: str | None = None


@dataclass(frozen=True)
class Cohort:
    seed: int
    students: tuple[Student, ...]
    unlinked_guardian_id: str

    def by_label(self, label: str) -> Student:
        return next(s for s in self.students if s.label == label)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, indent=1)


def _t(day: int, minute: int) -> str:
    return (BASE_DAY + timedelta(days=day, minutes=minute)).isoformat()


def zhixing_answer(label: str, item_id: str, quality: str, points: list[dict]) -> str:
    """按采分点拼一段合成作答。quality：core（学科术语）/ accepted（可接受表述）/ colloquial（口语）/ mixed（首点术语、其余口语）"""
    parts = []
    for i, p in enumerate(points):
        fam = p["keyword_family"]
        q = quality if quality != "mixed" else ("core" if i == 0 else "colloquial")
        words = fam.get(q) or fam.get("colloquial") or fam["core"]
        parts.append(f"{words[0]}")
    body = "；".join(parts)
    return f"（合成作答{label}）结合材料看，{body}。"


def essay_text(label: str, rng: random.Random) -> str:
    text = f"（合成作文{label}）" + rng.choice(ESSAY_OPENINGS) + rng.choice(ESSAY_MIDDLES) + rng.choice(ESSAY_ENDINGS)
    assert all(a in text for a in ESSAY_ANCHORS.values())
    return text


def generate(zhixing_points: dict[str, list[dict]], seed: int = SEED, size: int = SIZE) -> Cohort:
    """zhixing_points：知行内容包里各题的采分点（replay 从 adapters/zhixing/content/pack.json 读入后传进来）。"""
    rng = random.Random(seed)
    students = []
    households: list[tuple[str, str]] = []
    for n in range(1, size + 1):
        label = f"S{n:02d}"
        pattern = PATTERNS[(n - 1) % len(PATTERNS)]
        grade = (7, 8, 9)[rng.randrange(3)]
        birth_year = {9: 2012, 8: 2013, 7: 2014}[grade]
        birth = f"{birth_year}-{rng.randrange(1, 13):02d}-01"
        # 前 8 名两两同一家庭（兄弟姐妹）
        if n <= 8 and n % 2 == 0:
            household_id, guardian_id = households[-1]
        else:
            household_id, guardian_id = sid("household", label), sid("guardian", label)
            households.append((household_id, guardian_id))
        enrollment = CORE_SUBJECTS + (("echo",) if rng.random() < 0.3 else ())
        steps, retest = _script(label, pattern, rng, zhixing_points)
        students.append(Student(
            label=label, student_id=sid("student", label), account_id=sid("student-account", label),
            household_id=household_id, guardian_id=guardian_id, grade=grade, birth_year_month=birth,
            pattern=pattern, enrollment=enrollment, steps=tuple(steps), retest_answers=retest,
            interrupted_after=steps[-1].step_id if pattern == "interrupted" else None,
        ))
    return Cohort(seed=seed, students=tuple(students), unlinked_guardian_id=sid("guardian", "unlinked"))


def _script(label: str, pattern: str, rng: random.Random, zx_points: dict[str, list[dict]]) -> tuple[list[Step], dict]:
    correct = pattern in ("all_correct", "hint_dependent", "same_item_repeat")
    hint = HEAVY_HINT if pattern == "hint_dependent" else 0
    gap_area = rng.choice(sorted(AYA_BANK))
    # ---- 数学诊断：两组各取题；系统性缺口 = 某一组全答成陷阱答案
    aya_diag = []
    minute = 0
    for area in sorted(AYA_BANK):
        for item, right, trap in rng.sample(AYA_BANK[area], 2):
            wrong_here = pattern == "systematic_gap" and area == gap_area
            aya_diag.append(Attempt(item, {"text": trap if wrong_here else right}, _t(0, minute), hint))
            minute += 3
    # ---- 语文诊断：三道客观题
    wl_diag = []
    for i, (item, right, wrong) in enumerate(rng.sample(WENLI_OBJECTIVE, 3)):
        answer = right if correct or (pattern == "interrupted" and i == 0) else wrong
        wl_diag.append(Attempt(item, answer, _t(1, i * 2), hint))
    # ---- 数学批改：刷同题 = 同一道题连做三次
    area_items = AYA_BANK[gap_area if pattern == "systematic_gap" else rng.choice(sorted(AYA_BANK))]
    if pattern == "same_item_repeat":
        item, right, _ = area_items[0]
        aya_grades = [Attempt(item, {"text": right}, _t(2, k * 2), 0) for k in range(3)]
    else:
        picks = rng.sample(area_items, 2)
        aya_grades = [Attempt(it, {"text": (right if correct else trap)}, _t(2, k * 5), hint)
                      for k, (it, right, trap) in enumerate(picks)]
    # ---- 道法批改：一道主观题
    zx_item = rng.choice(ZHIXING_ITEMS)
    quality = {"all_correct": "core", "hint_dependent": "core", "same_item_repeat": "accepted",
               "systematic_gap": "colloquial", "interrupted": "mixed"}[pattern]
    zx = Attempt(zx_item, {"text": zhixing_answer(label, zx_item, quality, zx_points[zx_item])}, _t(3, 0), hint)
    # ---- 语文作文
    essay = Attempt(rng.choice(ESSAYS), {"text": essay_text(label, rng)}, _t(4, 0), 0)

    steps = [Step("aya-diagnose", "diagnose", "aya", tuple(aya_diag), mode="intake"),
             Step("wenli-diagnose", "diagnose", "wenli", tuple(wl_diag), mode="intake")]
    if pattern == "interrupted":
        # 中断：做完两科诊断与道法一题就离开（三科都有事件，但没有数学批改、作文与复测）
        steps.append(Step("zhixing-grade-1", "grade", "zhixing", (zx,)))
        return steps, {}
    steps += [Step(f"aya-grade-{k + 1}", "grade", "aya", (a,)) for k, a in enumerate(aya_grades)]
    steps.append(Step("zhixing-grade-1", "grade", "zhixing", (zx,)))
    steps.append(Step("wenli-grade-1", "grade", "wenli", (essay,)))
    # 复测时怎么答：缺口学生复测答对（体现“复测写入同一学生模型”），其余保持原样
    retest = {
        "aya": {item: {"text": right} for area in AYA_BANK.values() for item, right, _ in area},
        "zhixing_quality": "accepted" if pattern == "systematic_gap" else quality,
    }
    return steps, retest

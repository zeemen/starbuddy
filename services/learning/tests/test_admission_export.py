"""阿鸦准入决定文件的平台导出（specs/release-gate-sprint4 第 7 节、待人确认 #9；第二部分 PR E）。

纯函数：平台状态 → 决定文件的映射与“副本只能比平台更严”；与阿鸦格式 3（adapters/aya/src/pack/admission.ts）、迁移 0039 的常量核对。
库上：一次性库里合成的阿鸦内容版本（地区 synth-adx），合成 staff 签字执行准入，经审核台读身份导出；草稿拒绝导出；角色不对拒绝连接。
全部合成数据（规则 8）。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from pathlib import Path

import pytest

from review_console import admission_export as ax

ROOT = Path(__file__).resolve().parents[3]
CUR = {"region": "synth-adx", "exam_year": 2031, "grade": 8, "textbook_version": "syn-adx", "subject": "aya", "content_version": "0.1.0"}
CV = "c0ad0000-0000-4000-8000-0000000000c1"
AT = dt.datetime(2031, 3, 1, 8, 30, 15, 123456, tzinfo=dt.timezone.utc)
SRC = {"source_ref": "SYN-SRC-ADX-1", "source_type": "textbook", "license_status": "cleared"}
NONE = {"source_ref": None, "source_type": "unknown", "license_status": "unverified"}


def rid(n: int) -> str:
    return f"c0ad0000-0000-4000-8000-{n:012d}"


def snap(items: list[dict], status: str = "published", **ver) -> dict:
    return {"version": {"id": CV, **CUR, "status": status, "map_version_id": rid(999), **ver}, "items": items}


def item(i: str, adm: str, src: dict = NONE) -> dict:
    return {"item_id": i, "admission": adm, **src}


def apply(n: int, i: str, to: str, level: str | None = None, frm: str = "pending_review", minute: int = 0) -> dict:
    return {"id": rid(100 + n), "line": "admission", "action": "apply", "curriculum_version_id": CV, "item_id": i, "from_admission": frm,
            "to_admission": to, "review_level": level, "applies_review_id": rid(n), "decided_at": AT + dt.timedelta(minutes=minute)}


IDS = {f"SYN-ADX-{n}" for n in range(1, 9)}


def build(items, recs, **kw):
    return ax.build_decisions(snap(items, **kw), recs, CUR, IDS, AT)


# ---------- 映射 ----------
def test_mapping_and_report():
    items = [item("SYN-ADX-1", "admitted", SRC), item("SYN-ADX-2", "pending_review", SRC), item("SYN-ADX-3", "pending_review"),
             item("SYN-ADX-4", "rejected", {**NONE, "license_status": "denied"}), item("SYN-ADX-5", "source_unverified")]
    recs = [apply(1, "SYN-ADX-1", "pending_review", "Q1", "source_unverified", 0), apply(2, "SYN-ADX-1", "admitted", "Q2", minute=1),
            apply(3, "SYN-ADX-2", "pending_review", "Q1", "source_unverified"), apply(4, "SYN-ADX-3", "pending_review", None, "source_unverified"),
            apply(5, "SYN-ADX-4", "rejected", None, "source_unverified"),
            {"id": rid(500), "line": "content_version", "action": "publish", "curriculum_version_id": CV, "item_id": None,
             "decided_at": AT}]
    f, r = build(items, list(reversed(recs)))  # 不依赖输入顺序
    assert f["format"] == "aya-admission-decisions/1" and f["curriculum"] == CUR
    assert f["exported_at"] == "2031-03-01T08:30:15Z" and f["export_ref"] == "AYA-ADM-20310301T083015Z"
    assert re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,63}$", f["export_ref"])
    assert f["decisions"] == [
        {"item_id": "SYN-ADX-1", "admission": "admitted", **SRC, "review_level": "Q2", "review_ids": [rid(2)]},
        {"item_id": "SYN-ADX-2", "admission": "pending_review", **SRC, "review_level": "Q1", "review_ids": [rid(3)]},
        {"item_id": "SYN-ADX-4", "admission": "rejected", **NONE, "license_status": "denied", "review_level": None, "review_ids": [rid(5)]},
    ]
    # 来源未核的 pending_review：阿鸦格式 3 表达不了，回到缺省（更严），报告里列出；source_unverified 不写
    assert r["pending_review_as_default"] == ["SYN-ADX-3"] and r["pending_review_as_default_count"] == 1
    assert r["platform_admission"] == {"admitted": 1, "pending_review": 2, "rejected": 1, "source_unverified": 1}
    assert r["exported"] == {"admitted": 1, "pending_review": 1, "rejected": 1} and r["status"] == "published"
    assert "signer" not in json.dumps(f) and "signer" not in json.dumps(r)  # 文件与报告不含签字人
    assert ax.dumps(build(items, recs)[0]) == ax.dumps(f)  # 确定性


def test_pending_review_without_apply_record_goes_to_default():
    f, r = build([item("SYN-ADX-1", "pending_review", SRC)], [])
    assert f["decisions"] == [] and r["pending_review_as_default"] == ["SYN-ADX-1"]


def test_latest_apply_wins_for_downgrade():
    recs = [apply(1, "SYN-ADX-1", "admitted", "Q3", minute=0), apply(2, "SYN-ADX-1", "rejected", None, "admitted", minute=5)]
    f, _ = build([item("SYN-ADX-1", "rejected", SRC)], recs)
    assert f["decisions"][0]["review_ids"] == [rid(2)] and f["decisions"][0]["admission"] == "rejected"


@pytest.mark.parametrize("items,recs,kw,pattern", [
    ([item("SYN-ADX-1", "admitted", SRC)], [apply(1, "SYN-ADX-1", "admitted", "Q2")], {"status": "draft"}, "只有已发布"),
    ([item("SYN-ADX-1", "admitted", SRC)], [apply(1, "SYN-ADX-1", "admitted", "Q2")], {"status": "retired"}, "只有已发布"),
    ([], [], {"content_version": "0.2.0"}, "六段标识不一致"),
    ([], [], {"subject": "wenli"}, "只导出阿鸦"),
])
def test_refuses_version(items, recs, kw, pattern):
    with pytest.raises(ax.ExportError, match=pattern):
        build(items, recs, **kw)


@pytest.mark.parametrize("items,recs,pattern", [
    ([item("SYN-ADX-1", "admitted", SRC)], [], "不是升为 admitted"),
    ([item("SYN-ADX-1", "admitted", SRC)], [apply(1, "SYN-ADX-1", "admitted", "Q2", minute=0),
                                            apply(2, "SYN-ADX-1", "rejected", None, "admitted", minute=1)], "不是升为 admitted"),
    ([item("SYN-ADX-1", "admitted", SRC)], [apply(1, "SYN-ADX-1", "admitted", "Q1")], "低于 Q2"),
    ([item("SYN-ADX-1", "admitted", SRC)], [apply(1, "SYN-ADX-1", "admitted", None)], "低于 Q2"),
    ([item("SYN-ADX-1", "admitted", {**SRC, "license_status": "unverified"})], [apply(1, "SYN-ADX-1", "admitted", "Q2")], "来源未核"),
    ([item("SYN-ADX-1", "admitted", {**SRC, "source_type": "unknown"})], [apply(1, "SYN-ADX-1", "admitted", "Q2")], "来源未核"),
    ([item("SYN-ADX-1", "rejected")], [], "不是改为 rejected"),
    ([item("SYN-ADX-1", "admitted", {**SRC, "source_ref": "https://x"})], [apply(1, "SYN-ADX-1", "admitted", "Q2")], "来源字段不合格式"),
    ([item("SYN-ADX-1", "admitted", SRC)], [{**apply(1, "SYN-ADX-1", "admitted", "Q2"), "curriculum_version_id": rid(7)}],
     "不是升为 admitted"),  # 别的内容版本的执行记录不算
    ([item("SYN-ADX-99", "source_unverified")], [], "不在内容包里"),
])
def test_refuses_inconsistent_platform_data(items, recs, pattern):
    """会让副本比平台宽或平台数据不自洽：整份拒绝导出，不降级、不猜（缺省 source_unverified 不比 rejected 严）。"""
    with pytest.raises(ax.ExportError) as e:
        build(items, recs)
    assert any(re.search(pattern, p) for p in e.value.problems + [str(e.value)])


# ---------- 重建后的内容包逐题不比平台宽 ----------
def test_check_not_more_lenient():
    s = snap([item("SYN-ADX-1", "admitted", SRC), item("SYN-ADX-2", "pending_review"), item("SYN-ADX-3", "rejected")])
    ok = pk([{"id": "SYN-ADX-1", "admission": "admitted"}, {"id": "SYN-ADX-2", "admission": "source_unverified"},
             {"id": "SYN-ADX-3", "admission": "rejected"}, {"id": "SYN-ADX-4", "admission": "source_unverified"}])
    r = ax.check_not_more_lenient(ok, s)
    assert r["ok"] and (r["same"], r["stricter"]) == (3, 1)
    for i, a in (("SYN-ADX-2", "admitted"), ("SYN-ADX-3", "source_unverified"), ("SYN-ADX-4", "pending_review")):
        bad = pk([{"id": i, "admission": a}])
        r = ax.check_not_more_lenient(bad, s)
        assert not r["ok"] and r["violations"][0]["reason"] == "more_lenient_than_platform", (i, a)
    assert r["status"] == "published" and r["note"] is None


@pytest.mark.parametrize("status", ["retired", "draft"])
def test_check_unpublished_version_caps_at_source_unverified(status):
    """B-1：平台内容版本不是已发布时，学生看不到它的准入；内容包里比 source_unverified 宽的题全部不合格（即使平台上同样宽）。"""
    s = snap([item("SYN-ADX-1", "admitted", SRC), item("SYN-ADX-2", "pending_review"), item("SYN-ADX-3", "rejected")], status=status)
    r = ax.check_not_more_lenient(pk([{"id": "SYN-ADX-1", "admission": "admitted"}, {"id": "SYN-ADX-2", "admission": "pending_review"},
                                      {"id": "SYN-ADX-3", "admission": "rejected"}, {"id": "SYN-ADX-4", "admission": "source_unverified"}]), s)
    assert not r["ok"] and r["violation_count"] == 2 and r["status"] == status and status in r["note"]
    assert [(v["item_id"], v["reason"]) for v in r["violations"]] == [("SYN-ADX-1", "version_not_published"),
                                                                      ("SYN-ADX-2", "version_not_published")]
    # 不比 source_unverified 宽的照常比较：全部 source_unverified / rejected 合格；平台 rejected 而内容包 source_unverified 仍不合格
    ok = ax.check_not_more_lenient(pk([{"id": "SYN-ADX-1", "admission": "source_unverified"}, {"id": "SYN-ADX-3", "admission": "rejected"}]), s)
    assert ok["ok"] and (ok["same"], ok["stricter"]) == (1, 1)
    bad = ax.check_not_more_lenient(pk([{"id": "SYN-ADX-3", "admission": "source_unverified"}]), s)
    assert [v["reason"] for v in bad["violations"]] == ["more_lenient_than_platform"]


def pk(items, **over) -> dict:
    return {"format": "aya-content-pack/3", "curriculum": CUR, "pack_version": "x", "items": items, **over}


# ---------- 输入校验：比较、读库之前就拒（InputError，命令行退出码 2，不打堆栈） ----------
@pytest.mark.parametrize("pack,pattern", [
    (pk([{"id": "SYN-ADX-1", "admission": "published"}]), "不是已知取值"),
    (pk([{"id": "SYN-ADX-1"}]), "缺 admission"),
    (pk([{"admission": "admitted"}]), "缺题号"),
    (pk([{"id": "", "admission": "admitted"}]), "缺题号"),
    (pk(["SYN-ADX-1"]), "缺题号"),
    (pk([{"id": "SYN-ADX-1", "admission": "rejected"}, {"id": "SYN-ADX-1", "admission": "rejected"}]), "题号重复"),
    (pk([], curriculum=None), "缺 curriculum"),
    (pk([], curriculum={k: v for k, v in CUR.items() if k != "grade"}), "缺 grade"),
    (pk([], curriculum={**CUR, "exam_year": "2031"}), "exam_year 类型不对"),
    (pk([], curriculum={**CUR, "subject": "wenli"}), "不是阿鸦"),
    ({**pk([]), "items": None}, "缺题目列表"),
    (pk([], format="aya-content-pack/2"), "格式 3"),
    ([], "格式 3"),
])
def test_validate_pack(pack, pattern):
    with pytest.raises(ax.InputError, match=pattern):
        ax.validate_pack(pack)
    with pytest.raises(ax.InputError, match=pattern):
        ax.check_not_more_lenient(pack, snap([]))


@pytest.mark.parametrize("s,pattern", [
    (snap([item("SYN-ADX-1", "approved")]), "不是已知取值"),
    (snap([{"item_id": "SYN-ADX-1"}]), "缺 admission"),
    (snap([{"admission": "rejected"}]), "缺题号"),
    (snap([], status="archived"), "状态不是已知取值"),
    (snap([], region=None), "region 类型不对"),
    ({"items": []}, "缺内容版本"),
])
def test_validate_snapshot(s, pattern):
    with pytest.raises(ax.InputError, match=pattern):
        ax.check_not_more_lenient(pk([]), s)
    with pytest.raises(ax.InputError, match=pattern):
        ax.build_decisions(s, [], CUR, IDS, AT)


def test_check_refuses_six_segment_mismatch():
    with pytest.raises(ax.InputError, match="六段标识不一致"):
        ax.check_not_more_lenient(pk([], curriculum={**CUR, "content_version": "0.2.0"}), snap([]))


def _db_env(monkeypatch, url="postgresql://adx@/never?host=/nonexistent-socket-dir"):
    monkeypatch.setenv("REVIEW_CONSOLE_ENV", "test")
    monkeypatch.setenv("REVIEW_CONSOLE_KGE_DATABASE_URL", url)


@pytest.mark.parametrize("text", [
    json.dumps(pk([{"id": "SYN-ADX-1", "admission": "published"}])),
    json.dumps(pk([{"id": "SYN-ADX-1"}])),
    json.dumps(pk([], curriculum=None)),
    "{not json",
    json.dumps({"format": "aya-content-pack/3"}),
])
@pytest.mark.parametrize("cmd", ["export", "verify"])
def test_main_bad_pack_exit_2_without_traceback(cmd, text, tmp_path, monkeypatch, capsys):
    """内容包不对：读库之前就拒，退出码 2，一行简短说明，不打堆栈；不建输出文件。"""
    _db_env(monkeypatch)
    (tmp_path / "pack.json").write_text(text, encoding="utf-8")

    async def never(*_a, **_k):
        raise AssertionError("不应连库")
    monkeypatch.setattr(ax, "_connect", never)
    extra = ["--out", str(tmp_path / "o.json")] if cmd == "export" else []
    assert ax.main([cmd, "--pack", str(tmp_path / "pack.json"), *extra]) == 2
    err = capsys.readouterr().err
    assert "输入不对" in err and "Traceback" not in err and len(err.strip().splitlines()) == 1
    assert not (tmp_path / "o.json").exists()


# ---------- 输出路径与写入顺序（不连库：替换 _export） ----------
FILE = {"format": ax.FORMAT, "decisions": []}
REPORT = {"six_segment": "x", "status": "published", "platform_items": 0, "decisions": 0, "exported": {},
          "pending_review_as_default_count": 0, "export_ref": "AYA-ADM-20310301T083015Z"}


@pytest.fixture
def fake_export(monkeypatch):
    calls: list[str] = []

    async def f(pack_path):
        calls.append(pack_path)
        return FILE, REPORT
    monkeypatch.setattr(ax, "_export", f)
    return calls


def test_report_exists_refused_before_reading_platform(tmp_path, fake_export, capsys):
    out, rep = tmp_path / "d.json", tmp_path / "r.json"
    rep.write_text("old report\n", encoding="utf-8")
    assert ax.main(["export", "--pack", "p.json", "--out", str(out), "--report", str(rep)]) == 2
    assert fake_export == [] and not out.exists() and rep.read_text(encoding="utf-8") == "old report\n"
    assert "--replace" in capsys.readouterr().err
    out.write_text("old\n", encoding="utf-8")
    assert ax.main(["export", "--pack", "p.json", "--out", str(out)]) == 2 and fake_export == []
    assert ax.main(["export", "--pack", "p.json", "--out", str(out), "--report", str(out)]) == 2 and fake_export == []
    assert ax.main(["export", "--pack", "p.json", "--out", str(tmp_path / "no-dir" / "d.json")]) == 2 and fake_export == []
    # 加 --replace：决定文件与报告都原子替换，不留临时文件
    assert ax.main(["export", "--pack", "p.json", "--out", str(out), "--report", str(rep), "--replace"]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == FILE and json.loads(rep.read_text(encoding="utf-8")) == REPORT
    assert sorted(p.name for p in tmp_path.iterdir()) == ["d.json", "r.json"]


def test_replace_failure_removes_temp_file_and_keeps_old(tmp_path, fake_export, monkeypatch, capsys):
    out = tmp_path / "d.json"
    out.write_text("old\n", encoding="utf-8")

    def boom(src, dst):
        raise PermissionError("synthetic")
    monkeypatch.setattr(ax.os, "replace", boom)
    assert ax.main(["export", "--pack", "p.json", "--out", str(out), "--replace"]) == 2
    assert out.read_text(encoding="utf-8") == "old\n" and [p.name for p in tmp_path.iterdir()] == ["d.json"]
    assert "读写文件失败" in capsys.readouterr().err


def test_write_failure_without_replace_leaves_no_partial_file(tmp_path, monkeypatch):
    out = tmp_path / "d.json"
    real_open = open

    class Broken:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

        def write(self, _t):
            raise OSError("synthetic disk full")

    monkeypatch.setattr("builtins.open", lambda *a, **k: Broken(real_open(*a, **k)))
    with pytest.raises(OSError):
        ax._write(str(out), "x", False)
    assert not out.exists()


# ---------- 只连本机（PR D 的 local_only 与 check_server_local） ----------
@pytest.mark.parametrize("url", ["postgresql://u@db.example.com/x", "postgresql://u@localhost,10.1.2.3/x",
                                 "postgresql://u@/x?host=db.example.com", "postgresql://u@localhost/x?hostaddr=10.1.2.3"])
@pytest.mark.parametrize("cmd", ["export", "verify"])
def test_non_local_url_refused_before_connect(cmd, url, tmp_path, monkeypatch, capsys):
    _db_env(monkeypatch, url)
    (tmp_path / "pack.json").write_text(json.dumps(pk([])), encoding="utf-8")

    async def never(*_a, **_k):
        raise AssertionError("不应连库")
    import asyncpg
    monkeypatch.setattr(asyncpg, "connect", never)
    extra = ["--out", str(tmp_path / "o.json")] if cmd == "export" else []
    assert ax.main([cmd, "--pack", str(tmp_path / "pack.json"), *extra]) == 2
    err = capsys.readouterr().err
    assert "准入决定导出只连本机数据库" in err and "时政" not in err and "Traceback" not in err


@pytest.mark.parametrize("addr", ["10.0.0.5", "192.168.1.2", "2001:db8::1"])
def test_connected_server_not_local_refused(addr, monkeypatch):
    import asyncio

    import asyncpg

    class FakeConn:
        closed = False

        async def fetchval(self, _q):
            return addr

        async def close(self):
            FakeConn.closed = True

    async def connect(_url):
        return FakeConn()
    monkeypatch.setattr(asyncpg, "connect", connect)
    with pytest.raises(ax.NotLocalError, match="连上的服务端不是本机"):
        asyncio.run(ax._connect("postgresql://u@localhost/x"))
    assert FakeConn.closed


# ---------- 执行记录执行时间相同：分不出先后，拒绝导出 ----------
def test_same_decided_at_for_one_item_refused():
    recs = [apply(1, "SYN-ADX-1", "admitted", "Q2", minute=3), apply(2, "SYN-ADX-1", "rejected", None, "admitted", minute=3)]
    with pytest.raises(ax.ExportError) as e:
        build([item("SYN-ADX-1", "rejected", SRC)], recs)
    assert "执行时间相同" in str(e.value) and e.value.problems == ["题目 SYN-ADX-1 有多条执行时间相同的准入执行记录"]
    assert not isinstance(e.value, ax.InputError)
    # 不同题目同一时刻（一次执行多道题）照常
    ok = [apply(1, "SYN-ADX-1", "admitted", "Q2", minute=3), apply(2, "SYN-ADX-2", "rejected", None, minute=3)]
    f, _ = build([item("SYN-ADX-1", "admitted", SRC), item("SYN-ADX-2", "rejected")], ok)
    assert [d["item_id"] for d in f["decisions"]] == ["SYN-ADX-1", "SYN-ADX-2"]


# ---------- 与阿鸦格式 3、迁移 0039 的常量一致（两处各写一份，这里核对） ----------
def test_constants_match_aya_and_migration():
    ts = (ROOT / "adapters/aya/src/pack/admission.ts").read_text(encoding="utf-8")
    assert f'ADMISSION_DECISIONS_FORMAT = "{ax.FORMAT}"' in ts
    assert f'MIN_ADMITTED_REVIEW_LEVEL: ReviewLevel = "{ax.MIN_ADMITTED_REVIEW_LEVEL}"' in ts
    assert "REF_PATTERN = /" + ax.REF.pattern + "/;" in ts
    arr = lambda name: re.findall(r'"([a-z_]+)"', re.search(name + r" = \[(.*?)\] as const", ts).group(1))  # noqa: E731
    assert tuple(arr("SOURCE_TYPES")) == ax.SOURCE_TYPES and tuple(arr("LICENSE_STATUSES")) == ax.LICENSE_STATUSES
    assert set(arr("ADMISSIONS")) == set(ax.LENIENCY)
    keys = re.search(r"CURRICULUM_KEYS = \[(.*?)\] as const", ts).group(1)
    assert tuple(re.findall(r'"([a-z_]+)"', keys)) == ax.CURRICULUM_KEYS
    sql = (ROOT / "db/migrations/0039_platform__content_release.up.sql").read_text(encoding="utf-8")
    assert re.search(r"admission_min_review_rank\(p_subject platform.subject\).*?SELECT (\d) \$\$", sql, re.S).group(1) == \
        ax.MIN_ADMITTED_REVIEW_LEVEL[1:]
    assert f"source_ref text CHECK (source_ref ~ '{ax.REF.pattern}')" in sql


# ---------- 库上：审核台读身份 + 0042 只读函数 ----------
STAFF = uuid.UUID("5fad0000-0000-4000-8000-0000000005a1")
KGE_LOGIN = "adx_kge_app"


def _sign_apply(item_id: str, to: str, level: str | None, ref: str | None, typ: str | None, lic: str | None) -> str:
    args = ", ".join("NULL" if v is None else f"'{v}'" for v in (level, ref, typ, lic))
    return (f"SELECT platform.change_admission(ARRAY[platform.sign_admission('aya', '{item_id}', '{to}', 'approved', {args}, "
            f"platform.item_admission_digest('aya', '{item_id}'), 'test_fixture')]);")


@pytest.fixture(scope="module")
def adx(pg, tmp_path_factory):
    pg.psql(f"""
      CREATE ROLE {KGE_LOGIN} LOGIN IN ROLE starbuddy_kge;
      INSERT INTO platform.account (id, kind) VALUES ('{STAFF}', 'staff');
      SET ROLE starbuddy_learning;
      INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version)
        VALUES ('{CV}', 'synth-adx', 2031, 8, 'syn-adx', 'aya', '0.1.0');
      INSERT INTO platform.skill_node (subject, local_id, curriculum_version_id, label_code) VALUES ('aya', 'SYN-ADX-K1', '{CV}', 'SYN-ADX-K1');
      INSERT INTO platform.item (subject, item_id, curriculum_version_id, skill_local_id, admission) VALUES
        ('aya', 'SYN-ADX-1', '{CV}', 'SYN-ADX-K1', 'source_unverified'), ('aya', 'SYN-ADX-2', '{CV}', 'SYN-ADX-K1', 'source_unverified'),
        ('aya', 'SYN-ADX-3', '{CV}', 'SYN-ADX-K1', 'source_unverified'), ('aya', 'SYN-ADX-4', '{CV}', 'SYN-ADX-K1', 'source_unverified');
      RESET ROLE;""")
    pack = {"format": "aya-content-pack/3", "curriculum": CUR, "items": [{"id": f"SYN-ADX-{n}", "admission": "source_unverified"} for n in range(1, 5)]}
    d = tmp_path_factory.mktemp("adx")
    (d / "pack.json").write_text(json.dumps(pack), encoding="utf-8")
    return d


def _env(monkeypatch, pg, user: str) -> None:
    monkeypatch.setenv("REVIEW_CONSOLE_ENV", "test")
    monkeypatch.setenv("REVIEW_CONSOLE_KGE_DATABASE_URL", f"postgresql://{user}@/{pg.db}?host={pg.work}")


def test_db_export_draft_refused_then_published(pg, adx, monkeypatch, capsys):
    _env(monkeypatch, pg, KGE_LOGIN)
    out = adx / "decisions.json"
    assert ax.main(["export", "--pack", str(adx / "pack.json"), "--out", str(out)]) == 1  # 草稿：拒绝导出
    assert not out.exists() and "只有已发布" in capsys.readouterr().err
    helper = (ROOT / "db/seeds/dev/syn-publish-content.sql").read_text(encoding="utf-8")
    pg.psql(helper + f"\nSELECT pg_temp.syn_publish_content('{CV}');")
    pg.psql(f"SET ROLE starbuddy_review;\nSELECT set_config('app.account_id', '{STAFF}', false);\n"
            + _sign_apply("SYN-ADX-1", "pending_review", "Q1", "SYN-SRC-ADX-1", "textbook", "cleared")
            + _sign_apply("SYN-ADX-1", "admitted", "Q2", None, None, "cleared")
            + _sign_apply("SYN-ADX-2", "pending_review", None, None, None, None)
            + _sign_apply("SYN-ADX-3", "rejected", None, None, None, "denied")
            + "SELECT set_config('app.account_id', '', false);\nRESET ROLE;")
    rep = adx / "report.json"
    assert ax.main(["export", "--pack", str(adx / "pack.json"), "--out", str(out), "--report", str(rep)]) == 0
    f, r = json.loads(out.read_text(encoding="utf-8")), json.loads(rep.read_text(encoding="utf-8"))
    assert [(d["item_id"], d["admission"], d["review_level"], d["source_ref"]) for d in f["decisions"]] == [
        ("SYN-ADX-1", "admitted", "Q2", "SYN-SRC-ADX-1"), ("SYN-ADX-3", "rejected", None, None)]
    assert all(len(d["review_ids"]) == 1 and uuid.UUID(d["review_ids"][0]).version == 4 for d in f["decisions"])
    assert r["pending_review_as_default"] == ["SYN-ADX-2"] and r["status"] == "published"
    # 已存在：不加 --replace 拒绝覆盖；加了原子替换
    assert ax.main(["export", "--pack", str(adx / "pack.json"), "--out", str(out)]) == 2
    assert ax.main(["export", "--pack", str(adx / "pack.json"), "--out", str(out), "--replace"]) == 0
    # 核对：按决定重建后的样子不比平台宽；把 pending_review 的题写成 admitted 就不合格
    rebuilt = {"format": "aya-content-pack/3", "curriculum": CUR, "pack_version": "x",
               "items": [{"id": d["item_id"], "admission": d["admission"]} for d in f["decisions"]]
               + [{"id": "SYN-ADX-2", "admission": "source_unverified"}, {"id": "SYN-ADX-4", "admission": "source_unverified"}]}
    (adx / "rebuilt.json").write_text(json.dumps(rebuilt), encoding="utf-8")
    capsys.readouterr()
    assert ax.main(["verify", "--pack", str(adx / "rebuilt.json")]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    rebuilt["items"][2]["admission"] = "admitted"
    (adx / "rebuilt.json").write_text(json.dumps(rebuilt), encoding="utf-8")
    assert ax.main(["verify", "--pack", str(adx / "rebuilt.json")]) == 1


def test_db_export_refuses_wrong_role(pg, adx, monkeypatch, capsys):
    """只用审核台读身份：只继承 starbuddy_learning 的登录角色（conftest 的 learning_app）连上就拒绝，不读任何数据。"""
    _env(monkeypatch, pg, "learning_app")
    assert ax.main(["export", "--pack", str(adx / "pack.json"), "--out", str(adx / "never.json")]) == 2
    assert "只继承 starbuddy_kge" in capsys.readouterr().err and not (adx / "never.json").exists()


def test_env_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("REVIEW_CONSOLE_ENV", "production")
    with pytest.raises(SystemExit, match="REVIEW_CONSOLE_ENV"):
        ax.main(["export", "--pack", str(tmp_path / "p.json"), "--out", str(tmp_path / "o.json")])

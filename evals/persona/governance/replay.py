"""录制回放（spec 设计要点 6 第 2 层）：CI 只回放、不调用模型。

录音 evals/persona/recordings/<persona_id>/<case_id>.json（格式见 schema/recording.schema.json）：一次真实模型对话的完整转写，附
Prompt 指纹（该角色全部登记 Prompt）、用例指纹、model_version、生成参数、录制人、日期、数据级别 L0。
录音由人在本地用 dev 密钥执行 make persona-record 生成并随 PR 提交；Claude Code 不持有密钥、不执行录制。
Prompt 或用例指纹变了而没有新录音 → 状态 outdated；没有录音 → missing。严格模式下两者都失败，提示“需要重新录制”。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from personas.registry import iter_prompts, prompt_fingerprint

from .cases import case_fingerprint, student_turns, turns_of
from .common import HERE, Env, Turn, to_turns

RECORDING_FORMAT = "persona-recording/1"
SCHEMA_PATH = HERE / "schema" / "recording.schema.json"


def persona_prompt_fingerprints(env: Env, pid: str) -> dict[str, str]:
    return {e.prompt_id: prompt_fingerprint(e, env.root) for e in iter_prompts(env.reg) if e.owner_id == pid and e.owner_type != "task"}


def persona_prompt_versions(env: Env, pid: str) -> dict[str, str]:
    return {e.prompt_id: e.version for e in iter_prompts(env.reg) if e.owner_id == pid and e.owner_type != "task"}


def recording_path(env: Env, pid: str, case_id: str) -> Path:
    return env.eval_path("recordings_dir", "evals/persona/recordings") / pid / f"{case_id}.json"


def validate_recording(doc: Any) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return [e.message for e in jsonschema.Draft202012Validator(schema).iter_errors(doc)]


def status(env: Env, pid: str, case: dict[str, Any], *, case_fp: str | None = None,
           student: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """一条必需录音的状态：valid / outdated / missing / invalid，附原因与录音里的 model_version"""
    path = recording_path(env, pid, case["case_id"])
    rel = path.relative_to(env.root).as_posix() if path.is_relative_to(env.root) else str(path)
    row: dict[str, Any] = {"persona_id": pid, "case_id": case["case_id"], "path": rel}
    if not path.is_file():
        return {**row, "status": "missing", "reason": "需要重新录制：没有录音（make persona-record，由持有 dev 密钥的人执行）"}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {**row, "status": "invalid", "reason": "录音不是合法 JSON"}
    bad = validate_recording(doc)
    if bad:
        return {**row, "status": "invalid", "reason": f"录音格式不合法：{bad[0]}"}
    row["model_version"] = doc["model_version"]
    want_p = persona_prompt_fingerprints(env, pid)
    if doc["prompt_fingerprints"] != want_p:
        return {**row, "status": "outdated", "reason": "需要重新录制：Prompt 指纹与录音不一致（Prompt 改了）"}
    if doc["prompt_versions"] != persona_prompt_versions(env, pid):
        return {**row, "status": "outdated", "reason": "需要重新录制：Prompt 版本与录音不一致"}
    if doc["case_fingerprint"] != (case_fp or case_fingerprint(case)):
        return {**row, "status": "outdated", "reason": "需要重新录制：用例指纹与录音不一致（用例改了）"}
    rec_student = [{"speaker": t["speaker"], "text": t["text"], "emotion_signal": bool(t.get("emotion_signal")),
                    "asks_identity": bool(t.get("asks_identity"))} for t in doc["transcript"] if t["speaker"] == "student"]
    if rec_student != (student if student is not None else student_turns(case)):
        return {**row, "status": "outdated", "reason": "需要重新录制：录音里的学生输入与用例不一致"}
    if doc["persona_id"] != pid or doc["case_id"] != case["case_id"]:
        return {**row, "status": "invalid", "reason": "录音的 persona_id 或 case_id 与文件位置不符"}
    return {**row, "status": "valid", "recording": doc}


def recorded_turns(rec: dict[str, Any]) -> list[Turn]:
    return to_turns(rec["transcript"])


def make_recording(env: Env, pid: str, case: dict[str, Any], transcript: list[dict[str, Any]], *, model_version: str,
                   params: dict[str, Any], recorded_by: str, recorded_at: str, synthetic: bool) -> dict[str, Any]:
    """组装一条录音（make persona-record 的录制脚本与测试共用；本冲刺只用来生成合成录音测试框架本身）"""
    return {"format": RECORDING_FORMAT, "persona_id": pid, "case_id": case["case_id"],
            "prompt_fingerprints": persona_prompt_fingerprints(env, pid), "prompt_versions": persona_prompt_versions(env, pid),
            "case_fingerprint": case_fingerprint(case), "model_version": model_version, "params": params,
            "recorded_by": recorded_by, "recorded_at": recorded_at, "data_level": "L0", "synthetic": synthetic, "transcript": transcript}


def persona_turns_for_student(case: dict[str, Any]) -> list[Turn]:
    return [t for t in turns_of(case) if t.speaker == "student"]

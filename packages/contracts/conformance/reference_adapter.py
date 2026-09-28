"""参考假适配器：按契约返回结果，可以扮演任何学科（spec 范围第 4 条）。

用途有两个：
1. 证明一致性测试与学科无关：同一套测试对它（扮演知行）和对阿鸦都能跑；
2. 变异测试：FAULTS 里每一种违约行为注入后，一致性测试都必须失败（mutation.py）。

只用标准库；只监听 127.0.0.1。题目、答案都是合成的（规则 8），与 fixtures/reference.yaml 对应。
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
from starbuddy_contract.contract import CONTRACT_VERSION, plugin_contract  # noqa: E402

NS = uuid.UUID("7e5f0000-0000-4000-8000-00000000c0de")
RUBRIC = "ref-verifier@1.0.0"
# 合成题目：答案、陷阱答案对应的跨科大类、能力点、平台准入状态
ITEMS = {
    "REF-1": {"answer": "12", "trap": ("13", "computation_or_grammar"), "node": "REF-N1", "admission": "source_unverified"},
    "REF-2": {"answer": "ab", "trap": ("ba", "reading_the_question"), "node": "REF-N2", "admission": "admitted"},
}
SECRET_CODE = "answer_key_ref_2"  # 夹具标注的秘密（编码形状，专门用来测泄露检查）

# 违约类型 → (说明, 必须由哪个测试发现)。mutation.py 会核对确实是这个测试失败，而不是碰巧被别的测试抓到
FAULTS = {
    "extra_field": ("结果里夹带自由文本字段", "test_case"),
    "dangling_id": ("维度引用不存在的证据 ID", "test_case"),
    "fabricated_text": ("把标准答案当成学生原文存进作答证据", "test_case"),
    "leak_secret": ("在编码字段里泄露夹具标注的秘密", "test_case"),
    "random_ids": ("证据 ID 每次随机，重试不幂等", "test_idempotency"),
    "no_conflict": ("同 key 不同请求体也照常返回 200", "test_idempotency"),
    "conflict_not_problem": ("409 错误体不是契约 Problem", "test_idempotency"),
    "unimplemented_200": ("未实现的方法返回 200", "test_unimplemented_methods_are_refused"),
    "accepts_bad_request": ("不校验请求", "test_bad_requests_are_rejected"),
    "problem_echo": ("错误体回显学生作答", "test_bad_requests_are_rejected"),
    "ascii_echo": ("错误体用 \\uXXXX 转义回显学生作答", "test_bad_requests_are_rejected"),
    "bad_request_500": ("坏请求返回 500", "test_bad_requests_are_rejected"),
    "error_content_type": ("错误体的 content-type 不是 application/problem+json", "test_bad_requests_are_rejected"),
    "wrong_subject_node": ("能力点用了别的学科前缀", "test_case"),
    "unregistered_node": ("能力点没有在平台登记", "test_case"),
    "rubric_mismatch": ("结果的 rubric_version 与请求不一致", "test_case"),
    "looser_semantics": ("未准入的题目也声明 formal、不复核", "test_case"),
    "admission_looser": ("证据的准入状态比平台登记更松", "test_case"),
    "over_max": ("分数超过满分", "test_case"),
    "nan": ("置信度是 NaN", "test_case"),
    "wrong_verdict": ("判对判错颠倒", "test_case"),
    "wrong_category": ("错因的跨科大类错了", "test_case"),
    "drop_appeal_reason": ("申诉结果不标 student_appeal", "test_case"),
    "ignore_guessed": ("答对但自评猜的，不降权", "test_case"),
    "wrong_outcome": ("诊断观测一律 demonstrated", "test_case"),
    "bad_capabilities": ("能力声明的契约版本不对", "test_capabilities_match_contract"),
    "no_auth": ("不校验服务间令牌", "test_requires_service_credential"),
    "async_new_job_id": ("异步模式下，同 key 重试返回新的 job_id", "test_idempotency"),
}
ASYNC_FAULTS = {"async_new_job_id"}  # 这些违约只在异步模式下有意义


def _problem(status: int, code: str, title: str, detail: str | None = None) -> dict:
    p = {"type": f"/problems/{code.lower()}", "title": title, "status": status, "code": code, "retryable": False}
    if detail:
        p["detail"] = detail[:500]
    return p


def _canonical(v) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class ReferenceAdapter:
    def __init__(self, subject: str, credential: str, fault: str | None = None, asynchronous: bool = False) -> None:
        if fault and fault not in FAULTS:
            raise ValueError(f"未知的违约类型 {fault}")
        self.subject, self.credential, self.fault = subject, credential, fault
        self.asynchronous = asynchronous or fault in ASYNC_FAULTS
        self.seen: dict[str, str] = {}
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    # ---------- 业务 ----------
    def _id(self, key: str, fp: str, label: str) -> str:
        if self.fault == "random_ids":
            return str(uuid.uuid4())
        return str(uuid.uuid5(NS, f"{key}|{fp}|{label}"))

    def _judge(self, item_id: str, text: str) -> tuple[str, list[str]]:
        it = ITEMS[item_id]
        if text == it["answer"]:
            verdict, cats = "correct", []
        elif text == it["trap"][0]:
            verdict, cats = "incorrect", ["knowledge" if self.fault == "wrong_category" else it["trap"][1]]
        elif text == "?":
            verdict, cats = "needs_review", []
        else:
            verdict, cats = "incorrect", []
        if self.fault == "wrong_verdict" and verdict in ("correct", "incorrect"):
            verdict = "incorrect" if verdict == "correct" else "correct"
        return verdict, cats

    def _evidence(self, key, fp, i, a):
        item_id = a["item_ref"].split(":", 1)[1]
        it = ITEMS[item_id]
        prefix = "wenli" if self.fault == "wrong_subject_node" and self.subject != "wenli" else self.subject
        node = "REF-N9" if self.fault == "unregistered_node" else it["node"]
        admission = "admitted" if self.fault == "admission_looser" else it["admission"]
        common = {"occurred_at": a["occurred_at"], "item_ref": a["item_ref"], "skill_node": f"{prefix}:{node}",
                  "admission": admission}
        text = it["answer"] if self.fault == "fabricated_text" else a["response"].get("text", "")
        resp = {"evidence_id": self._id(key, fp, f"{i}:r"), "kind": "response_text", **common, "payload": {"text": text}}
        if CONTRACT_VERSION != "0.9.0" and "attempt_id" in a:  # v1.0：作答类证据指回所属作答（评审 N3）
            resp["attempt_id"] = a["attempt_id"]
        trace = {"evidence_id": self._id(key, fp, f"{i}:t"), "kind": "verifier_trace", **common,
                 "payload": {"sampled_points": 0, "agreeing_points": 0, "reason_codes": []}}
        return resp, trace, it

    def _envelope(self, evidence, extra_reasons, confidence):
        admitted = all(e["admission"] == "admitted" for e in evidence) or self.fault == "looser_semantics"
        reasons = list(dict.fromkeys(([] if admitted else ["content_not_admitted"]) + extra_reasons))
        env = {"contract_version": CONTRACT_VERSION, "evidence": evidence, "evidence_ids": [e["evidence_id"] for e in evidence],
               "confidence": confidence, "source_version": f"verifier:{RUBRIC};items:ref-pack@1.0.0",
               "model_version": f"algo:{RUBRIC}", "human_review_required": bool(reasons),
               "semantics": "formal" if admitted else "practice"}
        if reasons:
            env["review_reasons"] = reasons
        return env

    def grade(self, body, key, fp):
        a = body["attempt"]
        resp, trace, it = self._evidence(key, fp, 0, a)
        verdict, cats = self._judge(a["item_ref"].split(":", 1)[1], a["response"].get("text", ""))
        score = {"correct": 1, "incorrect": 0}.get(verdict)
        reasons = []
        if verdict == "needs_review":
            reasons += ["verifier_boundary_case", "dimension_unavailable"]
        if body.get("appeal") and self.fault != "drop_appeal_reason":
            reasons.append("student_appeal")
        conf = 0.4 if verdict == "needs_review" else 1
        if self.fault == "nan":
            conf = float("nan")
        if self.fault == "over_max" and score is not None:
            score = 5
        dim_id = SECRET_CODE if self.fault == "leak_secret" else "answer"
        dim_ids = [resp["evidence_id"], trace["evidence_id"]] if self.fault != "dangling_id" else [str(uuid.uuid4())]
        result = {
            "verdict": verdict,
            "rubric_version": "ref-verifier@0.9.9" if self.fault == "rubric_mismatch" else body["rubric_version"],
            "score": {"total": score, "max_total": 1, "calibrated": False, **({"excluded_dimensions": [dim_id]} if score is None else {})},
            "dimensions": [{"dimension_id": dim_id, "score": score, "max_score": 1, "judged_by": "verifier", "subjective": False,
                            "rule_ref": RUBRIC, "confidence": conf, "evidence_ids": dim_ids}],
            "error_attributions": [{"skill_node": resp["skill_node"], "subject_category": "ref_trap", "cross_subject_category": c,
                                    "confidence": 0.8, "evidence_ids": [resp["evidence_id"]], "algorithm_version": "ref-attr@1.0.0"}
                                   for c in cats],
            "verifier": {"verifier_id": "ref-verifier", "version": "1.0.0",
                         "reason_codes": ["parse_failure"] if verdict == "needs_review" else []},
        }
        if self.fault == "extra_field":
            result["commentary"] = "做得很好"
        return {**self._envelope([resp, trace], reasons, conf), "result": result}

    def diagnose(self, body, key, fp):
        evidence, obs = [], []
        for i, a in enumerate(body["attempts"]):
            resp, trace, it = self._evidence(key, fp, i, a)
            verdict, _ = self._judge(a["item_ref"].split(":", 1)[1], a["response"].get("text", ""))
            outcome = {"correct": "demonstrated", "incorrect": "not_demonstrated"}.get(verdict, "inconclusive")
            if self.fault == "wrong_outcome":
                outcome = "demonstrated"
            guessed = a.get("self_report") == "guessed" and self.fault != "ignore_guessed"
            conf = 0.3 if guessed and verdict == "correct" else 1
            evidence += [resp, trace]
            obs.append({"skill_node": resp["skill_node"], "outcome": outcome, "confidence": conf,
                        "evidence_ids": [resp["evidence_id"], trace["evidence_id"]], "algorithm_version": "ref-diag@1.0.0"})
        return {**self._envelope(evidence, [], min(o["confidence"] for o in obs)),
                "model_version": "algo:ref-diag@1.0.0", "result": {"observations": obs, "error_attributions": []}}

    def capabilities(self):
        m = {k: {"implemented": False} for k in ("diagnose", "plan", "teach", "grade", "generate_variation", "report")}
        m["grade"] = {"implemented": True, "async": self.asynchronous}
        m["diagnose"] = {"implemented": True, "async": self.asynchronous}
        return {"subject": self.subject, "adapter_version": "1.0.0",
                "contract_version": "0.8.0" if self.fault == "bad_capabilities" else CONTRACT_VERSION, "methods": m}

    # ---------- HTTP ----------
    def handle(self, verb: str, path: str, headers, raw: bytes) -> tuple[int, dict]:
        if self.fault != "no_auth":
            h = headers.get("authorization") or ""
            m = h[len("Bearer "):] if h.startswith("Bearer ") else ""
            if not m or not hmac.compare_digest(hashlib.sha256(m.encode()).digest(), hashlib.sha256(self.credential.encode()).digest()):
                return 401, _problem(401, "INVALID_REQUEST", "missing or invalid service token")
        if not path.startswith("/plugin/v0/"):
            return 404, _problem(404, "INVALID_REQUEST", "unknown route")
        name = path[len("/plugin/v0/"):]
        if verb == "GET" and name == "capabilities":
            return 200, self.capabilities()
        if verb == "GET" and name.startswith("jobs/"):
            job = self.jobs.get(name[len("jobs/"):])
            if not job:
                return 404, _problem(404, "JOB_NOT_FOUND", "no such job")
            return 200, job
        if verb != "POST":
            return 404, _problem(404, "INVALID_REQUEST", "unknown route")
        if name not in ("grade", "diagnose"):
            if self.fault == "unimplemented_200":
                return 200, {}
            return 501, _problem(501, "METHOD_NOT_IMPLEMENTED", f"{name} is not implemented")
        try:
            body = json.loads(raw)
        except ValueError:
            return 400, _problem(400, "INVALID_REQUEST", "invalid JSON")
        if self.fault != "accepts_bad_request":
            errs = plugin_contract().validate("GradeRequest" if name == "grade" else "DiagnoseRequest", body)
            if errs or body["context"]["subject"] != self.subject:
                attempts = [body["attempt"]] if "attempt" in body else body.get("attempts", [])
                text = next((a.get("response", {}).get("text") for a in attempts if a.get("response", {}).get("text")), "")
                detail = None
                if self.fault == "problem_echo":
                    detail = f"invalid answer: {text}"
                elif self.fault == "ascii_echo":
                    detail = f"invalid answer: {json.dumps(text)}"  # \uXXXX 转义
                if self.fault == "bad_request_500":
                    return 500, _problem(500, "INTERNAL", "internal error")
                return 400, _problem(400, "INVALID_REQUEST", "request does not match the contract", detail)
        ctx = body.get("context", {})
        key = ctx.get("idempotency_key", "")
        b = copy.deepcopy(body)
        b.get("context", {}).pop("request_id", None)
        fp = hashlib.sha256(_canonical(b).encode()).hexdigest()
        with self.lock:
            prev = self.seen.setdefault(f"{name}|{key}", fp)
        if prev != fp and self.fault != "no_conflict":
            if self.fault == "conflict_not_problem":
                return 409, {"error": "conflict"}
            return 409, _problem(409, "IDEMPOTENCY_CONFLICT", "idempotency_key was used with a different request body")
        result = (self.grade if name == "grade" else self.diagnose)(body, key, fp)
        if not self.asynchronous:
            return 200, result
        # 异步：同 key 同请求体返回同一个 job_id（契约 JobAccepted）
        job_id = str(uuid.uuid4()) if self.fault == "async_new_job_id" else self._id(key, fp, "job")
        self.jobs[job_id] = {"job_id": job_id, "method": name, "status": "succeeded", "usage_id": ctx.get("usage_id"),
                             "response": result}
        return 202, {"job_id": job_id, "method": name, "status": "queued", "poll_after_ms": 200,
                     "usage_id": ctx.get("usage_id"), "idempotency_key": key}


def serve(subject: str, credential: str, fault: str | None = None, asynchronous: bool = False) -> tuple[ThreadingHTTPServer, str]:
    """在后台线程启动，返回 (server, base_url)。"""
    adapter = ReferenceAdapter(subject, credential, fault, asynchronous)

    class Handler(BaseHTTPRequestHandler):
        def _go(self, verb):
            declared = self.headers.get("content-length") or "0"
            n = int(declared) if declared.isdigit() else -1
            if n < 0 or n > 1024 * 1024:
                status, body = 413, _problem(413, "INVALID_REQUEST", "request body too large or invalid length")
            else:
                status, body = adapter.handle(verb, self.path, self.headers, self.rfile.read(n) if n else b"")
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            error_type = "text/plain" if adapter.fault == "error_content_type" else "application/problem+json"
            self.send_header("content-type", error_type if status >= 400 else "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            self._go("GET")

        def do_POST(self):  # noqa: N802
            self._go("POST")

        def log_message(self, *args):  # 不打印请求（可能含作答）
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"

"""生成规范化 JSON 的共享测试向量（冲刺 2-E，specs/metering-impl-sprint2/spec.md 范围第 3 条）。

以 services/learning 的 canonical() 为准生成期望值；services/bff 的 TS 实现与学习服务的测试都读同一份向量。
在仓库根目录执行：python3 packages/contracts/scripts/gen_canonical_vectors.py
"""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, 'services', 'learning'))
os.chdir(ROOT)
from learning.fingerprint import canonical
inputs = [
 ("empty-object", '{}'),
 ("key-order", '{"b":1,"a":2,"c":{"z":null,"y":true,"x":false}}'),
 ("integers", '{"n":[0,-0,1,-1,42,9007199254740991,-9007199254740991]}'),
 ("floats", '{"f":[0.5,-0.25,0.1,123.456,1e-05,0.0001,1.5e-07,1234567.891,-98765.4321e-3,3.14159,1e-310]}'),
 ("strings-escape", '{"s":"quote\\" backslash\\\\ slash/ tab\\t nl\\n cr\\r bs\\b ff\\f nul\\u0000 us\\u001f del\\u007f"}'),
 ("strings-unicode", '{"s":"中文，全角：。 emoji 😀 é ñ \\u2028\\u2029"}'),
 ("keys-unicode-order", '{"é":1,"z":2,"中":3,"😀":4,"\\uff5e":5,"a":6,"A":7,"":8}'),
 ("float-edges", '{"f":[0.30000000000000004,5e-324,-1.7976931348623157e-300,2.2250738585072014e-308,0.1e-3]}'),
 ("c1-controls-and-escaped-keys", '{"\\u0080-\\u009f":"\u0080\u0085\u009f","key\\"quote":1,"key\\\\slash":2,"tab\\tkey":3}'),
 ("nested-arrays", '{"a":[[],[{}],[1,[2,[3]]],"x",null,true]}'),
 ("grade-request-shape", '{"method":"grade","body":{"context":{"subject":"aya","student_ref":"c1000000-0000-4000-8000-0000000000c1","idempotency_key":"b0b00000-0000-4000-8000-000000000001","locale":"zh-CN"},"attempt":{"item_ref":"aya:synthetic-0301-07","response":{"text":"2(x-1)"},"time_spent_ms":35000,"self_assessment":"guessed"},"appeal":false}}'),
]
out = []
for name, text in inputs:
    c = canonical(json.loads(text))
    out.append({"name": name, "input": text, "canonical": c, "sha256": hashlib.sha256(c.encode("utf-8")).hexdigest()})
doc = {"description": "规范化 JSON 的共享测试向量：services/learning 的 canonical()（Python json.dumps sort_keys、紧凑分隔符、ensure_ascii=False）与 services/bff 的 canonical() 必须逐字节一致。生成脚本 packages/contracts/scripts/gen_canonical_vectors.py。不含整数值的浮点数（1.0；绝对值 ≥ 2^53 的浮点数都属于这一类）、超出安全整数范围的整数、NaN、孤立代理项：两种语言对这些值的表示不同，BFF 的 canonical() 直接拒绝后两类。",
       "vectors": out}
json.dump(doc, open('packages/contracts/testdata/canonical-vectors.json','w'), ensure_ascii=False, indent=2)
open('packages/contracts/testdata/canonical-vectors.json','a').write('\n')
print(f'写出 {len(out)} 条向量')

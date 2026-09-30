# evals/voice-blind · 语音盲测评测框架

冲刺 4-I 第一部分（`specs/voice-blind-test-sprint4/spec.md`；方案与执行手册见同目录 `plan.md`）。评测即代码：
**不接任何 ASR / TTS / 发音评测供应商、不持有密钥、不联网、不读音频字节、不做任何声纹或音色分析**（测试断言）。
只用标准库 + PyYAML（测试另要 pytest）。

| 位置 | 内容 |
|---|---|
| `voiceblind/vendor.py` | 供应商适配接口（`AsrVendor`、`PronunciationVendor`，只有抽象类）、统一结果格式、提交与结果文件读写 |
| `voiceblind/manifest.py` | 输入清单 `voice-blind-manifest/1` 与校验 |
| `voiceblind/text.py` | 规范化：中文按字（全半角、标点、大小写、拉丁串可配），英文按词（大小写、标点、撇号、连字符可配） |
| `voiceblind/metrics.py` | 编辑距离、CER / WER、分位数（最近秩）、Pearson / Spearman（平均秩）、音素一致性 |
| `voiceblind/stats.py` | 自助法 95% 区间（按样本重抽，百分位最近秩，固定字符串种子） |
| `voiceblind/blind.py` | 供应商盲测编号（VA、VB…）、条目随机编号、每家提交顺序随机、编号表 sha256 |
| `voiceblind/report.py` | 报告（Markdown + JSON）：只含汇总、分组指标与样本编号；拒绝写进任何 `gold/` |
| `voiceblind/fakes.py` | 三家**合成**假供应商（准确 / 对口音样本系统性出错 / 随机出错），只用于流程样例 |
| `run_blind.py` | 入口：`sample`（合成样例）与 `score`（读执行环境里的结果文件） |
| `fixtures/manifest-synthetic.yaml` | 合成清单（24 条，音频只是 `synthetic://` 元数据引用） |
| `reports/sample-synthetic/` | 流程样例报告，报告头标“流程样例，不是评测结论”（测试核对它是最新的） |
| `tests/`、`scripts/mutations.py` | 手算一致、确定性、不含原文、无网络；27 条变异全部要被发现 |

## 输入清单
```yaml
format: voice-blind-manifest/1
synthetic: false                 # 合成清单写 true，只能用 synthetic:// 引用；真实清单只能用 store:// 引用
samples:
  - id: B1-0001                  # 随机样本编号（不得由姓名、学号等派生）
    audio: {ref: "store://batch1/B1-0001", duration_s: 3.2, sample_rate_hz: 16000, channels: 1}
    reference: "今天天气很好。"    # 朗读或对话脚本；可为空（静音 / 纯噪声样本，检查插入）；扫手机号、身份证号、邮箱
    lang: zh                     # zh | en
    accent: nanjing              # standard | nanjing | other（采集场景标签，不是推断）
    noise: classroom             # quiet | classroom | outdoor
    task: read                   # read | dialogue
    expert:                      # 可选：专家音素标注（发音评测用）
      phones: [j, in, t, ian]
      errors: [1]                # 专家标为发音错误的音素下标
      sentence_score: 82         # 0–100
```
不允许多余字段（例如姓名、年级、学校）；校验消息只含字段名与样本编号。

## 结果文件（执行环境里的中间产物，含转写原文：不进仓库，评测完删除）
`<编号>.asr.jsonl`：`{"sample_id", "text", "first_token_s", "total_s"}`；`<编号>.pron.jsonl`：`{"sample_id", "phones": [[音素, 对错]], "sentence_score"}`。

## 命令
```bash
# 更新合成样例报告（指标、规范化或假供应商改动后，测试会提示过期）
services/learning/.venv/bin/python evals/voice-blind/run_blind.py sample \
  --manifest evals/voice-blind/fixtures/manifest-synthetic.yaml --out evals/voice-blind/reports/sample-synthetic
# 真实盲测（境内执行环境里，见 plan.md 执行手册）
python run_blind.py score --manifest <清单> --results <结果目录（仓库外）> --out <报告目录> --key-sha256 <编号表 sha256>
# 测试 + 变异（缺依赖时非严格跳过、严格失败）
make voice-blind-eval-test PYTHON=services/learning/.venv/bin/python
```

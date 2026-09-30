"""语音盲测评测框架（冲刺 4-I，specs/voice-blind-test-sprint4）。

只做指标计算与报告：不接任何 ASR / TTS / 发音评测供应商，不持有密钥，不联网，不读取、不保存音频字节。
供应商适配接口见 vendor.py（只有抽象类）；合成假供应商见 fakes.py（只用于证明流程与指标计算）。
"""
FRAMEWORK_VERSION = "voice-blind@0.1.0"

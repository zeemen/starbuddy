import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for p in (ROOT / "tools/loadtest", ROOT / "tools/e2e-cohort", ROOT / "services/learning", ROOT / "services/learning/tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

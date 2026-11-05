"""Regenerate openapi.json from the application (run from the repository root)."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

spec = create_app(settings=Settings()).openapi()
(ROOT / "openapi.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"wrote openapi.json ({len(spec['paths'])} paths)")

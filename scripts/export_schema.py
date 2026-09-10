from __future__ import annotations

import json
from pathlib import Path

from canva_converter.models import DesignDocumentV1


target = Path(__file__).resolve().parents[1] / "schemas" / "design-document-v1.schema.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(DesignDocumentV1.model_json_schema(by_alias=True), indent=2), encoding="utf-8")
print(f"Exported Python Design IR schema to {target}")

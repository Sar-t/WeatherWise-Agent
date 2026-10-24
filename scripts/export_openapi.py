"""Write the API's OpenAPI schema to openapi.json (for the frontend's typed client).

Usage (from the repo root, venv active):
    python scripts/export_openapi.py [output path, default openapi.json]
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.api.app import create_app  # noqa: E402

if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")
    schema = create_app().openapi()  # no startup needed: the schema comes from the routes and models
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(schema['paths'])} paths, {len(schema.get('components', {}).get('schemas', {}))} schemas)")

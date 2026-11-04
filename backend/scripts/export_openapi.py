"""Write the API's OpenAPI document to a file (default: ../frontend/openapi.json).

The frontend's types are generated from that committed file (ADR-F05). CI runs this and
fails if the file changes, so the committed document always matches the backend code.

It writes to the file, not to stdout, because the app logs JSON to stdout at start-up.

Usage (from backend/): uv run python scripts/export_openapi.py [path]
"""

import json
import os
import sys
from pathlib import Path

# `create_app` refuses to start without a signing key; the document does not depend on it.
os.environ.setdefault("JWT_SECRET", "openapi-export-only-not-a-real-signing-key")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
# The committed document describes the default configuration, demo sign-in included
# (ADR-016), whatever the shell running the export has set.
os.environ["DEMO_LOGIN"] = "true"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import create_app  # noqa: E402

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    path.write_text(json.dumps(create_app().openapi(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}", file=sys.stderr)


if __name__ == "__main__":
    main()

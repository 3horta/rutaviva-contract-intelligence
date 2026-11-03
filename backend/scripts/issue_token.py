"""Print a test JWT for a seeded tenant.

    python scripts/issue_token.py --tenant tenant_a

Only the token goes to stdout, so it can be captured:
`TOKEN=$(python scripts/issue_token.py --tenant tenant_a)`. Errors go to stderr.
Runs inside the api container (working directory /app) or locally from `backend/`.
"""

import argparse
import sys
from pathlib import Path

# `python scripts/issue_token.py` puts `scripts/` on the path, not the backend root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.auth import AuthConfigError, issue_token  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.models import Tenant  # noqa: E402
from app.db.session import session_factory_for  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue a test JWT for a tenant.")
    parser.add_argument("--tenant", required=True, help="Tenant name, e.g. tenant_a")
    args = parser.parse_args()

    settings = get_settings()
    # The app role can read `tenants` (no RLS on that table).
    with session_factory_for(settings.database_url)() as session:
        tenant = session.scalar(select(Tenant).where(Tenant.name == args.tenant))
    if tenant is None:
        print(f"Unknown tenant: {args.tenant} (has the seed run?)", file=sys.stderr)
        return 1

    try:
        token = issue_token(settings, tenant.id, tenant.name)
    except AuthConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())

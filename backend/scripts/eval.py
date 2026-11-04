"""Behaviour-based evaluation against the RUNNING API (ADR-014).

For each tenant in the questions file: issue a token, upload its PDFs (duplicates are
fine: the API returns the existing document) and wait until each is `ready` or `failed`.
Then ask every question with `POST /query`, in a fresh conversation, and check the
behaviour: outcome, cited page, answer contains/lacks, no cross-tenant document, confidence
cap, warnings, plus global checks (non-empty, no internals, numbers grounded in quotes).
The checks live in `scripts/eval_lib.py`.

    make eval                      # in a one-off backend container, against the stack
    python scripts/eval.py --only carrier-detention-rate,customs-brokerage-absent
    python scripts/eval.py --kinds isolation,injection --repeat 3 --json out.json

Exit code 1 if any non-soft case fails (or is flaky over `--repeat`). Tokens are signed
with the key from JWT_SECRET / JWT_SECRET_FILE, for tenants looked up in the database
(DATABASE_URL); `--create-tenants` creates missing ones with MIGRATIONS_DATABASE_URL.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# `python scripts/eval.py` puts `scripts/` on the path, not the backend root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.dialects.postgresql import insert  # noqa: E402

from app.core.auth import AuthConfigError, issue_token  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.db.models import Tenant  # noqa: E402
from app.db.session import session_factory_for  # noqa: E402
from scripts.eval_lib import (  # noqa: E402
    ApiError,
    CaseResult,
    EvalSetError,
    exit_code,
    format_report,
    load_cases,
    results_as_json,
    run_cases,
    select_cases,
    upload_and_wait,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
PAGE_SIZE = 100  # the API's maximum `limit` for GET /documents


class HttpApi:
    """The `Api` protocol of eval_lib over httpx."""

    def __init__(self, base_url: str, timeout_s: float) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout_s)

    def _request(self, method: str, url: str, token: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(
                method, url, headers={"Authorization": f"Bearer {token}"}, **kwargs
            )
        except httpx.HTTPError as exc:
            raise ApiError(f"{method} {url}: {type(exc).__name__}: {exc}") from exc
        if response.status_code >= 400:
            # Problem Details: the title and detail are enough for a one-line reason.
            try:
                body = response.json()
                message = f"{body.get('title', '')}: {body.get('detail', '')}"
            except ValueError:
                message = response.text[:200]
            raise ApiError(f"{method} {url} -> {response.status_code} {message}")
        return response.json()

    def query(self, token: str, question: str) -> dict[str, Any]:
        return self._request("POST", "/query", token, json={"question": question})

    def list_documents(self, token: str) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        while True:
            page = self._request(
                "GET", "/documents", token, params={"limit": PAGE_SIZE, "offset": len(documents)}
            )
            documents += page["results"]
            if not page["results"] or len(documents) >= page["count"]:
                return documents

    def upload(self, token: str, path: Path) -> dict[str, Any]:
        with path.open("rb") as file:
            files = {"file": (path.name, file, "application/pdf")}
            return self._request("POST", "/documents", token, files=files)

    def get_document(self, token: str, document_id: str) -> dict[str, Any]:
        return self._request("GET", f"/documents/{document_id}", token)


def resolve_path(raw: str) -> Path:
    """Relative paths work from the repo root or from `backend/` (where uv runs)."""
    path = Path(raw)
    if path.is_absolute() or path.exists():
        return path
    return REPO_ROOT / path


def create_tenants(settings: Settings, names: list[str]) -> None:
    """Insert missing tenants (owner role: the app role can only read `tenants`)."""
    statement = insert(Tenant).values([{"name": n, "display_name": n} for n in names])
    engine = create_engine(settings.migrations_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(statement.on_conflict_do_nothing(index_elements=[Tenant.name]))
    finally:
        engine.dispose()


def issue_tokens(settings: Settings, names: list[str]) -> dict[str, str]:
    with session_factory_for(settings.database_url)() as session:
        tenants = session.scalars(select(Tenant).where(Tenant.name.in_(names))).all()
    found = {tenant.name: tenant for tenant in tenants}
    missing = sorted(set(names) - set(found))
    if missing:
        raise SystemExit(
            f"Unknown tenant(s): {', '.join(missing)}. The demo tenants come from "
            "`make seed`; for other tenants pass --create-tenants."
        )
    try:
        return {name: issue_token(settings, t.id, t.name) for name, t in found.items()}
    except AuthConfigError as exc:
        raise SystemExit(str(exc)) from None


def setup_documents(
    api: HttpApi, tokens: dict[str, str], pdfs_dir: Path, timeout_s: float
) -> dict[str, set[str]]:
    """Upload each tenant's PDFs, wait for them, and return each tenant's document ids."""
    for tenant, token in tokens.items():
        pdfs = sorted((pdfs_dir / tenant).glob("*.pdf"))
        if not pdfs:
            print(f"[setup] {tenant}: no PDFs in {pdfs_dir / tenant}", file=sys.stderr)
            continue
        print(f"[setup] {tenant}: uploading {len(pdfs)} PDFs and waiting (max {timeout_s:.0f}s)")
        states = upload_and_wait(api, token, pdfs, timeout_s)
        for name, state in sorted(states.items()):
            if state["status"] == "failed":
                print(f"[setup]   failed  {name}: {state.get('failure_code')}")
            elif state["status"] != "ready":
                print(f"[setup]   TIMEOUT {name}: still {state['status']}")
    own_ids = {}
    for tenant, token in tokens.items():
        own_ids[tenant] = {str(d["id"]) for d in api.list_documents(token)}
    return own_ids


def print_progress(result: CaseResult) -> None:
    print(f"  {result.status:<6} {result.case.id}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Behaviour-based evaluation of the API.")
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--questions", default="sample_data/questions.yaml")
    parser.add_argument(
        "--pdfs", default="sample_data/pdfs", help="One subfolder of PDFs per tenant"
    )
    parser.add_argument("--only", default="", help="Comma-separated case ids")
    parser.add_argument("--kinds", default="", help="Comma-separated kinds (lexical,...)")
    parser.add_argument("--repeat", type=int, default=1, help="Ask each case N times")
    parser.add_argument("--json", dest="json_out", help="Write the full results here")
    parser.add_argument("--no-upload", action="store_true", help="Use the documents as they are")
    parser.add_argument("--create-tenants", action="store_true", help="Create missing tenants")
    parser.add_argument("--ingest-timeout", type=float, default=600, help="Seconds to wait")
    parser.add_argument("--request-timeout", type=float, default=180, help="Seconds per call")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")

    try:
        cases = select_cases(
            load_cases(resolve_path(args.questions)),
            only=[s for s in args.only.split(",") if s],
            kinds=[s for s in args.kinds.split(",") if s],
        )
    except (EvalSetError, OSError) as exc:
        print(f"Questions file: {exc}", file=sys.stderr)
        return 2
    if not cases:
        print("No case selected.", file=sys.stderr)
        return 2

    settings = get_settings()
    tenants = sorted({case.tenant for case in cases})
    if args.create_tenants:
        create_tenants(settings, tenants)
    tokens = issue_tokens(settings, tenants)

    api = HttpApi(args.api, args.request_timeout)
    try:
        if args.no_upload:
            own_ids = {
                t: {str(d["id"]) for d in api.list_documents(tok)} for t, tok in tokens.items()
            }
        else:
            own_ids = setup_documents(api, tokens, resolve_path(args.pdfs), args.ingest_timeout)
    except ApiError as exc:
        print(f"[setup] {exc}", file=sys.stderr)
        return 2

    print(f"[eval] {len(cases)} case(s) x {args.repeat} against {args.api}")
    results = run_cases(api, cases, tokens, own_ids, repeat=args.repeat, on_result=print_progress)
    print()
    print(format_report(results))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results_as_json(results), indent=2) + "\n")
        print(f"\nFull results: {args.json_out}")
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())

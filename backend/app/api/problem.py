"""RFC 9457 Problem Details for every error the API returns. (Being implemented.)"""

from fastapi import FastAPI


def install_problem_handlers(app: FastAPI) -> None:
    """Register exception handlers that render `AppError` (and validation and unexpected
    errors) as `application/problem+json` with the current `trace_id`."""

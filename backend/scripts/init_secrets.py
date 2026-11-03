"""Create the JWT signing key on first start, if there is none.

Run by the `migrate` service before the migrations. Does nothing when:
- `JWT_SECRET` is set (the key comes from the environment), or
- `JWT_SECRET_FILE` already exists (a previous start created it).

Otherwise it writes `secrets.token_urlsafe(32)` to `JWT_SECRET_FILE` with mode 0600. In
Compose the file lives in the `secrets` volume; `migrate` and `api` run as the same
user (uid 1000, from the same image), so the API can read it and nobody else can.
The key is never printed or logged.

    python scripts/init_secrets.py
"""

import logging
import os
import secrets

from app.core.config import get_settings
from app.core.logging import configure_logging

logger = logging.getLogger("scripts.init_secrets")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    if settings.jwt_secret is not None and settings.jwt_secret.get_secret_value().strip():
        logger.info("secrets.jwt_from_env")
        return

    path = settings.jwt_secret_file
    if path.exists():
        logger.info("secrets.jwt_file_exists", extra={"path": str(path)})
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    # O_EXCL: if two processes race, the second one fails instead of overwriting.
    # The mode is applied at creation, so the key is never readable by others.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(secrets.token_urlsafe(32) + "\n")
    logger.info("secrets.jwt_file_created", extra={"path": str(path)})


if __name__ == "__main__":
    main()

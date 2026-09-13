"""``python -m reflect_kb.broker``: serve the Context Broker from environment config."""

from __future__ import annotations

import uvicorn

from .app import create_app, psycopg_store_factory
from .auth import OIDCVerifier
from .config import BrokerSettings


def main() -> None:
    settings = BrokerSettings.from_env()
    settings.assert_role()  # a superuser, BYPASSRLS or owner DSN fails here, before anything is served
    verifier = OIDCVerifier(settings.oidc())
    verifier.warm()  # a broken issuer fails here, before anything is served
    app = create_app(
        verifier=verifier,
        store_factory=psycopg_store_factory(settings.pg_dsn),
        resolver=settings.resolver(),
        max_limit=settings.max_limit,
    )
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()

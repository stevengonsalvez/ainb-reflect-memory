"""Context Broker: a read-only, OIDC-authenticated HTTP route over MemoryStore.

The broker exposes ``EvidencePack`` and nothing else. It never synthesizes an
answer; it returns evidence the caller's agent reasons over locally. Three
guards run on every request, in order:

1. Authentication. A bearer token is verified against the configured OIDC
   issuer (discovery + JWKS). No token is 401. The tenant is read from a
   verified claim; a token without that claim is 403. Nothing in the body or
   query can name a tenant.
2. Classification floor. Items labelled ``restricted`` or ``pii`` are never
   returned (they cannot exist in the shared store either, see migration
   0003; the handler filter is defence in depth).
3. Source pinning. Every returned hit carries ``repo@sha:path[#Lstart-Lend]``
   and a resolver has confirmed that commit and path exist. A hit whose
   source does not parse or does not resolve is dropped and counted.

``fastapi``, ``PyJWT`` and ``uvicorn`` come from the ``broker`` extra. This
package's ``pinning`` module is pure Python so ingest code can build pins
without the extra installed; import ``reflect_kb.broker.app`` for the server.
"""

from __future__ import annotations

__all__ = ["create_app"]


def create_app(*args, **kwargs):  # pragma: no cover - thin lazy shim
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)

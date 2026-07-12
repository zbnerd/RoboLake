"""Deterministic idempotency identifiers for application requests."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from robolake.domain.identifiers import Sha256Digest


def request_fingerprint(fields: Iterable[bytes]) -> Sha256Digest:
    """Hash length-delimited fields so concatenation boundaries remain unambiguous."""
    digest = hashlib.sha256()
    for field in fields:
        digest.update(len(field).to_bytes(8, byteorder="big", signed=False))
        digest.update(field)
    return Sha256Digest(digest.hexdigest())


def make_idempotency_key(operation: str, fields: Iterable[bytes]) -> str:
    """Create a namespaced key for one application operation."""
    fingerprint = request_fingerprint(fields)
    return f"robolake-m1:{operation}:{fingerprint.value}"

"""Behavior tests for deterministic idempotency fingerprints."""

from robolake.application.idempotency import make_idempotency_key, request_fingerprint


def test_idempotency_fingerprint_preserves_field_boundaries() -> None:
    fingerprint = request_fingerprint([b"ab", b"c"])

    assert fingerprint.value == "601d5476e2ccfe2c87a2bba7a322659734a05749d5b5aa781f513e4912db0d5f"
    assert fingerprint != request_fingerprint([b"a", b"bc"])
    assert make_idempotency_key("version-register", [b"ab", b"c"]) == (
        f"robolake-m1:version-register:{fingerprint.value}"
    )

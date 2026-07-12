"""Safe descriptor-relative streamed PUT tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
from robolake.application.contracts import FileIdentity, LocalFileRef, PresignedRequest, PutOutcome
from robolake.domain.errors import RetryableTransferError, SourceChangedError, TransferRejectedError
from robolake.domain.identifiers import RelativePath, Sha256Digest
from robolake.domain.manifest import ManifestEntry
from robolake.infrastructure.http_transfer import HttpByteTransfer


def _identity(path: Path) -> FileIdentity:
    value = path.stat()
    return FileIdentity(value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _source(tmp_path: Path, data: bytes = b"abc") -> tuple[LocalFileRef, ManifestEntry]:
    root = tmp_path / "source"
    root.mkdir()
    file = root / "data.bin"
    file.write_bytes(data)
    digest = Sha256Digest.parse(hashlib.sha256(data).hexdigest())
    return (
        LocalFileRef(root, _identity(root), ("data.bin",), _identity(file)),
        ManifestEntry(RelativePath.parse("data.bin"), len(data), digest),
    )


def test_http_put_streams_and_classifies_created_without_exposing_capability(
    tmp_path: Path,
) -> None:
    local_ref, entry = _source(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["If-None-Match"] == "*"
        assert request.read() == b"abc"
        return httpx.Response(200, headers={"ETag": '"etag"'})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transfer = HttpByteTransfer(client, chunk_size=2)
    capability = PresignedRequest(
        "http://storage.invalid/blob?X-Amz-Credential=private",
        {"If-None-Match": "*", "Content-Length": "3"},
    )

    receipt = transfer.put(local_ref, capability, entry)

    assert receipt.outcome is PutOutcome.CREATED
    assert receipt.etag == '"etag"'


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(412, PutOutcome.ALREADY_EXISTS), (409, PutOutcome.CONFLICT)],
)
def test_http_put_classifies_conditional_conflicts_without_parsing_body(
    tmp_path: Path, status_code: int, expected: PutOutcome
) -> None:
    local_ref, entry = _source(tmp_path)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status_code, text="provider secret body")
        )
    )
    transfer = HttpByteTransfer(client, chunk_size=2)

    receipt = transfer.put(
        local_ref,
        PresignedRequest("http://storage.invalid/blob?token=secret", {"Content-Length": "3"}),
        entry,
    )

    assert receipt.outcome is expected


def test_http_put_rejects_changed_source_before_network(tmp_path: Path) -> None:
    local_ref, entry = _source(tmp_path)
    (local_ref.root / "data.bin").write_bytes(b"changed")
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    transfer = HttpByteTransfer(httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(SourceChangedError):
        transfer.put(local_ref, PresignedRequest("http://private/?secret=yes", {}), entry)
    assert calls == 0


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [(500, RetryableTransferError), (429, RetryableTransferError), (400, TransferRejectedError)],
)
def test_http_put_translates_status_without_url_or_provider_body(
    tmp_path: Path, status_code: int, error_type: type[Exception]
) -> None:
    local_ref, entry = _source(tmp_path)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: (request.read(), httpx.Response(status_code, text="credential"))[1]
        )
    )
    transfer = HttpByteTransfer(client)

    with pytest.raises(error_type) as captured:
        transfer.put(
            local_ref,
            PresignedRequest("http://private/?X-Amz-Signature=secret", {"Content-Length": "3"}),
            entry,
        )
    assert "secret" not in str(captured.value)
    assert "credential" not in str(captured.value)
    assert str(local_ref.root) not in str(captured.value)

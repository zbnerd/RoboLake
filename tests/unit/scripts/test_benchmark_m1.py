"""Behavior tests for the reproducible M1 benchmark harness."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from robolake.application.contracts import PutOutcome, PutReceipt
from robolake.domain.errors import RetryableTransferError
from robolake.domain.identifiers import RelativePath, Sha256Digest
from robolake.domain.manifest import ManifestEntry
from robolake.infrastructure.scanner import FileSystemScanner

pytestmark = pytest.mark.unit


def _load_benchmark_module() -> ModuleType:
    path = Path(__file__).parents[3] / "scripts" / "benchmark_m1.py"
    spec = importlib.util.spec_from_file_location("robolake_benchmark_m1", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


benchmark = _load_benchmark_module()


def test_quick_profile_matrix_covers_m1_shapes_without_multigigabyte_defaults() -> None:
    program = """
import json
from scripts.benchmark_m1 import build_profiles

print(json.dumps([
    {
        "name": profile.name,
        "file_count": profile.file_count,
        "file_size_bytes": profile.file_size_bytes,
        "unique_file_count": profile.unique_file_count,
    }
    for profile in build_profiles("quick")
]))
"""

    completed = subprocess.run(
        [sys.executable, "-c", program],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    profiles = json.loads(completed.stdout)
    by_name = {profile["name"]: profile for profile in profiles}
    assert set(by_name) == {
        "many-small-files",
        "medium-files",
        "single-large-file",
        "duplicate-0-percent",
        "duplicate-50-percent",
        "duplicate-90-percent",
        "interrupted-file-resume",
    }
    assert by_name["duplicate-0-percent"]["unique_file_count"] == 40
    assert by_name["duplicate-50-percent"]["unique_file_count"] == 20
    assert by_name["duplicate-90-percent"]["unique_file_count"] == 4
    assert max(profile["file_size_bytes"] for profile in profiles) <= 64 * 1024 * 1024


def test_manual_large_profile_requires_explicit_size_below_m1_limit() -> None:
    profiles = benchmark.build_profiles("manual-large", manual_large_bytes=4_000_000_000)

    assert len(profiles) == 1
    assert profiles[0].name == "single-large-file-manual"
    assert profiles[0].file_count == 1
    assert profiles[0].unique_file_count == 1
    assert profiles[0].file_size_bytes == 4_000_000_000


def test_dataset_generation_is_deterministic_and_matches_manifest_metrics(tmp_path: Path) -> None:
    profile = benchmark.BenchmarkProfile("tiny-duplicate", 4, 1024, 2)

    first = benchmark.generate_dataset(tmp_path / "first", profile, seed_token="release-seed")
    second = benchmark.generate_dataset(tmp_path / "second", profile, seed_token="release-seed")
    first_scan = FileSystemScanner().scan(first.root)
    second_scan = FileSystemScanner().scan(second.root)

    assert first.file_count == 4
    assert first.logical_bytes == 4096
    assert first.unique_file_count == 2
    assert first.unique_bytes == 2048
    assert first.duplicate_percent == 50.0
    assert first_scan.manifest.file_count == first.file_count
    assert first_scan.manifest.logical_bytes == first.logical_bytes
    assert first_scan.manifest.unique_blob_count == first.unique_file_count
    assert first_scan.manifest.unique_blob_bytes == first.unique_bytes
    assert first_scan.manifest.canonical_bytes == second_scan.manifest.canonical_bytes


def test_result_schema_separates_snapshot_content_invocation_and_wall_clock() -> None:
    result = benchmark.BenchmarkResult(
        profile=benchmark.BenchmarkProfile("tiny", 4, 1024, 2),
        snapshot=benchmark.SnapshotMetrics(file_count=4, logical_bytes=4096),
        unique_content=benchmark.UniqueContentMetrics(blob_count=2, bytes=2048),
        scan=benchmark.ScanMetrics(
            duration_seconds=0.25,
            manifest_build_seconds=0.01,
            effective_hashing_mib_per_second=0.015625,
        ),
        operations=(
            benchmark.OperationMetrics(
                name="first-push",
                wall_clock_seconds=0.5,
                finalize_seconds=0.02,
                peak_process_rss_mib=42.0,
                control_plane_request_count=7,
                invocation_outcome=benchmark.InvocationMetrics(
                    created_blob_count=2,
                    created_blob_bytes=2048,
                    reused_blob_count=0,
                    reused_blob_bytes=0,
                ),
            ),
        ),
        database_query_count=None,
    )

    payload = result.as_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["snapshot"] == {"file_count": 4, "logical_bytes": 4096}
    assert payload["unique_content"] == {"blob_count": 2, "bytes": 2048}
    assert payload["operations"][0]["invocation_outcome"]["created_blob_bytes"] == 2048
    assert payload["operations"][0]["wall_clock_seconds"] == 0.5
    assert payload["operations"][0]["control_plane_request_count"] == 7
    assert payload["database_query_count"] is None
    assert "uploaded_bytes" not in encoded


def test_measuring_scanner_records_scan_and_manifest_rebuild_time(tmp_path: Path) -> None:
    profile = benchmark.BenchmarkProfile("tiny", 1, 1024, 1)
    generated = benchmark.generate_dataset(tmp_path / "source", profile, seed_token="seed")
    ticks = iter((10.0, 11.0, 20.0, 20.25))
    scanner = benchmark.MeasuringScanner(FileSystemScanner(), clock=lambda: next(ticks))

    scanned = scanner.scan(generated.root)

    assert scanned.manifest.logical_bytes == 1024
    assert scanner.metrics is not None
    assert scanner.metrics.duration_seconds == 1.0
    assert scanner.metrics.manifest_build_seconds == 0.25
    assert scanner.metrics.effective_hashing_mib_per_second == 1024 / (1024 * 1024)


def test_interrupting_transfer_reports_completed_receipts_before_one_interruption() -> None:
    entry = ManifestEntry(
        RelativePath.parse("data.bin"),
        3,
        Sha256Digest.parse("a" * 64),
    )

    class FakeTransfer:
        calls = 0

        def put(self, *_: object) -> PutReceipt:
            self.calls += 1
            return PutReceipt(PutOutcome.CREATED, None)

    delegate = FakeTransfer()
    transfer = benchmark.InterruptingTransfer(cast(Any, delegate), interrupt_on_call=2)

    transfer.put(cast(Any, object()), cast(Any, object()), entry)
    with pytest.raises(RetryableTransferError, match="benchmark interruption"):
        transfer.put(cast(Any, object()), cast(Any, object()), entry)

    assert delegate.calls == 1
    assert transfer.invocation_metrics == benchmark.InvocationMetrics(1, 3, 0, 0)


def test_measuring_control_plane_records_finalize_wall_time_and_request_count() -> None:
    sentinel = object()

    class FakeControl:
        def finalize(self, _: object) -> object:
            return sentinel

    ticks = iter((5.0, 5.125))
    control = benchmark.MeasuringControlPlane(cast(Any, FakeControl()), clock=lambda: next(ticks))

    result = control.finalize(cast(Any, object()))

    assert result is sentinel
    assert control.finalize_seconds == 0.125
    assert control.request_count == 1


def test_measuring_control_plane_counts_and_delegates_every_control_request() -> None:
    class FakeControl:
        def __getattr__(self, name: str) -> Any:
            return lambda *_args, **_kwargs: name

    control = benchmark.MeasuringControlPlane(cast(Any, FakeControl()))
    opaque = cast(Any, object())

    assert control.create_dataset(opaque, "key") == "create_dataset"
    assert control.register_version(opaque, opaque, "key") == "register_version"
    assert control.resolve(opaque) == "resolve"
    assert control.status(opaque) == "status"
    assert control.manifest(opaque) == "manifest"
    assert control.prepare_upload(opaque, opaque, "key") == "prepare_upload"
    assert control.upload_url(opaque) == "upload_url"
    assert control.complete_upload(opaque, None) == "complete_upload"
    assert control.download_plan(opaque, None) == "download_plan"
    assert control.request_count == 9


def test_script_entrypoint_defines_profile_builder_before_executing_main(tmp_path: Path) -> None:
    script = Path(__file__).parents[3] / "scripts" / "benchmark_m1.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--mode",
            "manual-large",
            "--manual-large-bytes",
            "1",
            "--workspace",
            str(tmp_path / "workspace"),
            "--output",
            str(tmp_path / "result.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "Manual large profile must be above 64 MiB" in completed.stderr
    assert "NameError" not in completed.stderr

#!/usr/bin/env python3
"""Reproducible M1 benchmark profiles and runner."""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import resource
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

import httpx
from robolake.application.contracts import (
    DownloadCapability,
    LocalFileRef,
    PresignedRequest,
    PutOutcome,
    PutReceipt,
    ScannedDataset,
    UploadPreparation,
)
from robolake.application.ports import ByteTransferPort, ControlPlanePort, ScannerPort
from robolake.application.pull import PullWorkflow
from robolake.application.push import PushWorkflow
from robolake.domain.errors import ManifestMismatchError, RetryableTransferError
from robolake.domain.identifiers import DatasetName, DatasetReference, Sha256Digest
from robolake.domain.manifest import Manifest, ManifestEntry
from robolake.domain.records import DatasetRecord, VersionRecord, VersionStatus
from robolake.infrastructure.api_client import RoboLakeApiClient
from robolake.infrastructure.atomic_tree import AtomicTreeDownloader
from robolake.infrastructure.http_transfer import HttpByteTransfer
from robolake.infrastructure.scanner import FileSystemScanner
from robolake.infrastructure.settings import Settings

_WRITE_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class BenchmarkProfile:
    """One synthetic regular-file-tree shape."""

    name: str
    file_count: int
    file_size_bytes: int
    unique_file_count: int

    def __post_init__(self) -> None:
        if (
            self.file_count < 1
            or self.file_size_bytes < 1
            or self.unique_file_count < 1
            or self.unique_file_count > self.file_count
        ):
            raise ValueError("Benchmark profile counts and sizes must be positive and consistent.")


@dataclass(frozen=True, slots=True)
class GeneratedDataset:
    """Expected logical and deduplicated totals for generated synthetic files."""

    root: Path
    file_count: int
    logical_bytes: int
    unique_file_count: int
    unique_bytes: int
    duplicate_percent: float


@dataclass(frozen=True, slots=True)
class SnapshotMetrics:
    file_count: int
    logical_bytes: int


@dataclass(frozen=True, slots=True)
class UniqueContentMetrics:
    blob_count: int
    bytes: int


@dataclass(frozen=True, slots=True)
class InvocationMetrics:
    created_blob_count: int
    created_blob_bytes: int
    reused_blob_count: int
    reused_blob_bytes: int


@dataclass(frozen=True, slots=True)
class ScanMetrics:
    duration_seconds: float
    manifest_build_seconds: float
    effective_hashing_mib_per_second: float


@dataclass(frozen=True, slots=True)
class OperationMetrics:
    name: str
    wall_clock_seconds: float
    finalize_seconds: float | None
    peak_process_rss_mib: float
    control_plane_request_count: int
    invocation_outcome: InvocationMetrics | None


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    profile: BenchmarkProfile
    snapshot: SnapshotMetrics
    unique_content: UniqueContentMetrics
    scan: ScanMetrics
    operations: tuple[OperationMetrics, ...]
    database_query_count: int | None

    def as_dict(self) -> dict[str, Any]:
        """Serialize metrics without conflating created content and wire traffic."""
        return asdict(self)


class MeasuringScanner:
    """Record production scan time and an isolated canonical-manifest rebuild."""

    def __init__(
        self,
        delegate: ScannerPort,
        *,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        self._delegate = delegate
        self._clock = clock
        self.metrics: ScanMetrics | None = None

    def scan(self, source: Path) -> ScannedDataset:
        started = self._clock()
        scanned = self._delegate.scan(source)
        duration = self._clock() - started
        manifest_started = self._clock()
        rebuilt = Manifest.build(file.entry for file in scanned.files)
        manifest_duration = self._clock() - manifest_started
        if rebuilt.canonical_bytes != scanned.manifest.canonical_bytes:
            raise ManifestMismatchError("Benchmark manifest replay differs from scanner output.")
        logical_mib = scanned.manifest.logical_bytes / (1024 * 1024)
        throughput = logical_mib / duration if duration > 0 else 0.0
        self.metrics = ScanMetrics(duration, manifest_duration, throughput)
        return scanned


class InterruptingTransfer:
    """Inject one deterministic pre-PUT interruption for resume measurement."""

    def __init__(self, delegate: ByteTransferPort, *, interrupt_on_call: int) -> None:
        if interrupt_on_call < 1:
            raise ValueError("Interruption call must be positive.")
        self._delegate = delegate
        self._interrupt_on_call = interrupt_on_call
        self._calls = 0
        self._interrupted = False
        self._created_count = 0
        self._created_bytes = 0
        self._reused_count = 0
        self._reused_bytes = 0

    @property
    def invocation_metrics(self) -> InvocationMetrics:
        return InvocationMetrics(
            self._created_count,
            self._created_bytes,
            self._reused_count,
            self._reused_bytes,
        )

    def put(
        self,
        local_ref: LocalFileRef,
        request: PresignedRequest,
        expected_entry: ManifestEntry,
    ) -> PutReceipt:
        self._calls += 1
        if self._calls == self._interrupt_on_call and not self._interrupted:
            self._interrupted = True
            raise RetryableTransferError("Synthetic benchmark interruption before PUT.")
        receipt = self._delegate.put(local_ref, request, expected_entry)
        if receipt.outcome is PutOutcome.CREATED:
            self._created_count += 1
            self._created_bytes += expected_entry.size_bytes
        else:
            self._reused_count += 1
            self._reused_bytes += expected_entry.size_bytes
        return receipt


class MeasuringControlPlane:
    """Record control-plane request count and time spent in finalization."""

    def __init__(
        self,
        delegate: ControlPlanePort,
        *,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        self._delegate = delegate
        self._clock = clock
        self.request_count = 0
        self.finalize_seconds: float | None = None

    def begin_operation(self) -> None:
        self.request_count = 0
        self.finalize_seconds = None

    def create_dataset(self, name: DatasetName, idempotency_key: str) -> DatasetRecord:
        self.request_count += 1
        return self._delegate.create_dataset(name, idempotency_key)

    def register_version(
        self, dataset_id: UUID, manifest: Manifest, idempotency_key: str
    ) -> VersionRecord:
        self.request_count += 1
        return self._delegate.register_version(dataset_id, manifest, idempotency_key)

    def resolve(self, reference: DatasetReference) -> VersionRecord:
        self.request_count += 1
        return self._delegate.resolve(reference)

    def status(self, version_id: UUID) -> VersionStatus:
        self.request_count += 1
        return self._delegate.status(version_id)

    def manifest(self, version_id: UUID) -> Manifest:
        self.request_count += 1
        return self._delegate.manifest(version_id)

    def prepare_upload(
        self,
        version_id: UUID,
        digest: Sha256Digest,
        idempotency_key: str,
    ) -> UploadPreparation:
        self.request_count += 1
        return self._delegate.prepare_upload(version_id, digest, idempotency_key)

    def upload_url(self, session_id: UUID) -> PresignedRequest:
        self.request_count += 1
        return self._delegate.upload_url(session_id)

    def complete_upload(self, session_id: UUID, etag: str | None) -> UploadPreparation:
        self.request_count += 1
        return self._delegate.complete_upload(session_id, etag)

    def download_plan(self, version_id: UUID, cursor: str | None) -> DownloadCapability:
        self.request_count += 1
        return self._delegate.download_plan(version_id, cursor)

    def finalize(self, version_id: UUID) -> VersionRecord:
        self.request_count += 1
        started = self._clock()
        result = self._delegate.finalize(version_id)
        self.finalize_seconds = self._clock() - started
        return result


@dataclass(slots=True)
class _Runtime:
    scanner: MeasuringScanner
    control: MeasuringControlPlane
    transfer: HttpByteTransfer
    control_client: RoboLakeApiClient
    transfer_http: httpx.Client

    def close(self) -> None:
        self.transfer_http.close()
        self.control_client.close()


def _build_runtime(settings: Settings) -> _Runtime:
    control_http = httpx.Client(base_url=settings.api_url, timeout=httpx.Timeout(30.0))
    transfer_http = httpx.Client(
        timeout=httpx.Timeout(connect=30.0, read=600.0, write=None, pool=30.0)
    )
    control_client = RoboLakeApiClient(control_http)
    return _Runtime(
        scanner=MeasuringScanner(
            FileSystemScanner(
                max_single_put_bytes=settings.max_single_put_bytes,
                chunk_size=settings.stream_chunk_bytes,
            )
        ),
        control=MeasuringControlPlane(control_client),
        transfer=HttpByteTransfer(transfer_http, chunk_size=settings.stream_chunk_bytes),
        control_client=control_client,
        transfer_http=transfer_http,
    )


def _peak_rss_mib() -> float:
    value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value / (1024 * 1024) if sys.platform == "darwin" else value / 1024


def _invocation(result: Any) -> InvocationMetrics:
    return InvocationMetrics(
        created_blob_count=result.created_blob_count,
        created_blob_bytes=result.created_blob_bytes,
        reused_blob_count=result.reused_blob_count,
        reused_blob_bytes=result.reused_blob_bytes,
    )


def _operation(
    name: str,
    started: float,
    control: MeasuringControlPlane,
    invocation: InvocationMetrics | None,
) -> OperationMetrics:
    return OperationMetrics(
        name=name,
        wall_clock_seconds=perf_counter() - started,
        finalize_seconds=control.finalize_seconds,
        peak_process_rss_mib=_peak_rss_mib(),
        control_plane_request_count=control.request_count,
        invocation_outcome=invocation,
    )


def run_profile(
    profile: BenchmarkProfile,
    *,
    workspace: Path,
    run_id: str,
    settings: Settings,
) -> BenchmarkResult:
    """Measure one profile through the real M1 scanner, API, storage, and pull workflow."""
    profile_root = workspace / profile.name
    source = profile_root / "source"
    output = profile_root / "restored"
    generated = generate_dataset(source, profile, seed_token=run_id)
    runtime = _build_runtime(settings)
    dataset = DatasetName.parse(f"benchmark/{run_id}/{profile.name}")
    operations: list[OperationMetrics] = []
    first_result: Any | None = None
    first_scan: ScanMetrics | None = None
    try:
        if profile.name == "interrupted-file-resume":
            runtime.control.begin_operation()
            interrupted_transfer = InterruptingTransfer(runtime.transfer, interrupt_on_call=2)
            interrupted_push = PushWorkflow(
                runtime.scanner,
                runtime.control,
                interrupted_transfer,
                lambda _event: None,
            )
            started = perf_counter()
            try:
                interrupted_push.run(source, dataset)
            except RetryableTransferError as error:
                if "Synthetic benchmark interruption" not in str(error):
                    raise
            else:
                raise RuntimeError("Benchmark interruption profile completed unexpectedly.")
            first_scan = runtime.scanner.metrics
            operations.append(
                _operation(
                    "interrupted-push",
                    started,
                    runtime.control,
                    interrupted_transfer.invocation_metrics,
                )
            )

            runtime.control.begin_operation()
            resume_push = PushWorkflow(
                runtime.scanner,
                runtime.control,
                runtime.transfer,
                lambda _event: None,
            )
            started = perf_counter()
            first_result = resume_push.run(source, dataset)
            operations.append(
                _operation(
                    "resume-push",
                    started,
                    runtime.control,
                    _invocation(first_result),
                )
            )
        else:
            push = PushWorkflow(
                runtime.scanner,
                runtime.control,
                runtime.transfer,
                lambda _event: None,
            )
            runtime.control.begin_operation()
            started = perf_counter()
            first_result = push.run(source, dataset)
            first_scan = runtime.scanner.metrics
            operations.append(
                _operation(
                    "first-push",
                    started,
                    runtime.control,
                    _invocation(first_result),
                )
            )

            runtime.control.begin_operation()
            started = perf_counter()
            repeated = push.run(source, dataset)
            operations.append(
                _operation(
                    "identical-re-push",
                    started,
                    runtime.control,
                    _invocation(repeated),
                )
            )

        if first_result is None or first_scan is None:
            raise RuntimeError("Benchmark push did not produce complete metrics.")
        if (
            first_result.file_count != generated.file_count
            or first_result.logical_bytes != generated.logical_bytes
            or first_result.unique_blob_count != generated.unique_file_count
            or first_result.unique_blob_bytes != generated.unique_bytes
        ):
            raise RuntimeError("Benchmark push totals differ from generated synthetic data.")

        runtime.control.begin_operation()
        pull = PullWorkflow(runtime.control, AtomicTreeDownloader(runtime.transfer))
        started = perf_counter()
        pulled = pull.run(first_result.reference, output)
        if (
            pulled.materialized_file_count != generated.file_count
            or pulled.materialized_bytes != generated.logical_bytes
        ):
            raise RuntimeError("Benchmark pull totals differ from generated synthetic data.")
        operations.append(_operation("pull", started, runtime.control, None))
        return BenchmarkResult(
            profile=profile,
            snapshot=SnapshotMetrics(generated.file_count, generated.logical_bytes),
            unique_content=UniqueContentMetrics(
                generated.unique_file_count,
                generated.unique_bytes,
            ),
            scan=first_scan,
            operations=tuple(operations),
            database_query_count=None,
        )
    finally:
        runtime.close()


def _command_output(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError:
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _machine_metadata() -> dict[str, Any]:
    page_size = os.sysconf("SC_PAGE_SIZE")
    page_count = os.sysconf("SC_PHYS_PAGES")
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "logical_cpu_count": os.cpu_count(),
        "memory_bytes": page_size * page_count,
        "python": platform.python_version(),
    }


def _software_metadata() -> dict[str, Any]:
    images_raw = _command_output(["docker", "compose", "images", "--format", "json"])
    try:
        images = json.loads(images_raw) if images_raw is not None else None
    except json.JSONDecodeError:
        images = None
    return {
        "git_commit": _command_output(["git", "rev-parse", "HEAD"]),
        "docker_engine": _command_output(["docker", "version", "--format", "{{.Server.Version}}"]),
        "docker_compose": _command_output(["docker", "compose", "version", "--short"]),
        "uv": _command_output(["uv", "--version"]),
        "compose_images": images,
    }


def run_benchmark(
    *,
    mode: str,
    workspace: Path,
    output: Path,
    run_id: str,
    manual_large_bytes: int,
) -> dict[str, Any]:
    """Execute selected profiles and persist one JSON evidence document."""
    if not run_id or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in run_id
    ):
        raise ValueError(
            "Run ID must contain lowercase ASCII letters, digits, hyphen, or underscore."
        )
    workspace.mkdir(parents=True, exist_ok=False)
    profiles = build_profiles(mode, manual_large_bytes=manual_large_bytes)
    settings = Settings()
    results: list[dict[str, Any]] = []
    for profile in profiles:
        print(f"benchmarking {profile.name}", file=sys.stderr, flush=True)
        results.append(
            run_profile(profile, workspace=workspace, run_id=run_id, settings=settings).as_dict()
        )
    report = {
        "schema_version": 1,
        "benchmark": "RoboLake M1",
        "mode": mode,
        "run_id": run_id,
        "measured_at_utc": datetime.now(UTC).isoformat(),
        "machine": _machine_metadata(),
        "software": _software_metadata(),
        "measurement_notes": {
            "scan_duration_includes_traversal_hashing_and_the_production_manifest_build": True,
            "manifest_build_is_a_separate_replay_over_scanned_entries": True,
            "effective_hashing_rate_uses_logical_bytes_over_total_scan_duration": True,
            "peak_rss_is_the_process_high_water_mark_and_is_monotonic_within_this_run": True,
            "database_query_count_unavailable_without_api_process_instrumentation": True,
            "created_blob_bytes_are_not_wire_bytes": True,
        },
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure the frozen RoboLake M1 workflow.")
    parser.add_argument("--mode", choices=("quick", "manual-large"), default="quick")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", default=uuid4().hex)
    parser.add_argument("--manual-large-bytes", type=int, default=4_000_000_000)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.workspace is not None:
        run_benchmark(
            mode=args.mode,
            workspace=args.workspace,
            output=args.output,
            run_id=args.run_id,
            manual_large_bytes=args.manual_large_bytes,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="robolake-benchmark-") as temporary:
            run_benchmark(
                mode=args.mode,
                workspace=Path(temporary) / "workspace",
                output=args.output,
                run_id=args.run_id,
                manual_large_bytes=args.manual_large_bytes,
            )
    print(args.output)
    return 0


def build_profiles(
    mode: str, *, manual_large_bytes: int = 4_000_000_000
) -> tuple[BenchmarkProfile, ...]:
    """Return bounded quick profiles; multi-gigabyte work is never implicit."""
    if mode == "manual-large":
        if not 64 * 1024 * 1024 < manual_large_bytes <= 5_000_000_000:
            raise ValueError("Manual large profile must be above 64 MiB and within the M1 limit.")
        return (BenchmarkProfile("single-large-file-manual", 1, manual_large_bytes, 1),)
    if mode != "quick":
        raise ValueError("Benchmark mode must be quick or manual-large.")
    return (
        BenchmarkProfile("many-small-files", 512, 4 * 1024, 512),
        BenchmarkProfile("medium-files", 8, 4 * 1024 * 1024, 8),
        BenchmarkProfile("single-large-file", 1, 64 * 1024 * 1024, 1),
        BenchmarkProfile("duplicate-0-percent", 40, 256 * 1024, 40),
        BenchmarkProfile("duplicate-50-percent", 40, 256 * 1024, 20),
        BenchmarkProfile("duplicate-90-percent", 40, 256 * 1024, 4),
        BenchmarkProfile("interrupted-file-resume", 4, 2 * 1024 * 1024, 4),
    )


def generate_dataset(
    root: Path,
    profile: BenchmarkProfile,
    *,
    seed_token: str,
) -> GeneratedDataset:
    """Generate deterministic opaque regular files using bounded memory."""
    root.mkdir(parents=True, exist_ok=False)
    unique_paths: list[Path] = []
    for index in range(profile.file_count):
        path = root / f"group-{index % 16:02d}" / f"file-{index:06d}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        if index < profile.unique_file_count:
            _write_deterministic_file(
                path,
                profile.file_size_bytes,
                seed=f"{seed_token}:{profile.name}:{index}",
            )
            unique_paths.append(path)
        else:
            shutil.copyfile(unique_paths[index % profile.unique_file_count], path)
    logical_bytes = profile.file_count * profile.file_size_bytes
    unique_bytes = profile.unique_file_count * profile.file_size_bytes
    duplicate_percent = (1 - profile.unique_file_count / profile.file_count) * 100
    return GeneratedDataset(
        root=root,
        file_count=profile.file_count,
        logical_bytes=logical_bytes,
        unique_file_count=profile.unique_file_count,
        unique_bytes=unique_bytes,
        duplicate_percent=duplicate_percent,
    )


def _write_deterministic_file(path: Path, size_bytes: int, *, seed: str) -> None:
    generator = random.Random(seed)
    remaining = size_bytes
    with path.open("xb") as stream:
        while remaining:
            chunk_size = min(remaining, _WRITE_CHUNK_BYTES)
            stream.write(generator.randbytes(chunk_size))
            remaining -= chunk_size


if __name__ == "__main__":
    raise SystemExit(main())

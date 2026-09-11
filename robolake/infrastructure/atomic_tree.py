"""Private staged reconstruction with atomic whole-directory publication."""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from robolake.application.contracts import DownloadItem, PreparedDownload
from robolake.domain.errors import (
    ContentConflictError,
    ManifestMismatchError,
    OutputExistsError,
    UnsafePathError,
)
from robolake.domain.manifest import Manifest, ManifestEntry
from robolake.infrastructure.atomic_publish import publish_no_replace
from robolake.infrastructure.http_transfer import HttpByteTransfer


@dataclass(slots=True)
class _PreparedState:
    entry: ManifestEntry
    descriptor: int
    part_path: Path
    final_path: Path


class AtomicTreeDownloader:
    """Reconstruct inside one private sibling and publish it without replacement."""

    def __init__(
        self,
        transfer: HttpByteTransfer,
        *,
        publisher: Callable[[Path, Path], None] = publish_no_replace,
    ) -> None:
        self._transfer = transfer
        self._publisher = publisher
        self._requested_output: Path | None = None
        self._output: Path | None = None
        self._staging: Path | None = None
        self._staging_parent: Path | None = None
        self._manifest: Manifest | None = None
        self._prepared: dict[UUID, _PreparedState] = {}
        self._completed: set[str] = set()

    def begin(self, output: Path, manifest: Manifest) -> None:
        """Validate the complete manifest/output before creating private staging."""
        if self._staging is not None:
            raise ContentConflictError("A tree download is already active.")
        canonical = Manifest.from_canonical_bytes(manifest.canonical_bytes)
        if canonical.sha256 != manifest.sha256:
            raise ManifestMismatchError("Manifest digest does not match its canonical bytes.")
        absolute_output = output.absolute()
        parent = absolute_output.parent
        _require_existing_non_symlink_directory(parent)
        try:
            os.lstat(absolute_output)
        except FileNotFoundError:
            pass
        else:
            raise OutputExistsError("Output already exists; it will not be replaced.")
        staging = parent / f".robolake-{absolute_output.name}-{uuid4().hex}.tmp"
        os.mkdir(staging, mode=0o700)
        os.chmod(staging, 0o700)
        self._requested_output = output
        self._output = absolute_output
        self._staging = staging
        self._staging_parent = parent.resolve(strict=True)
        self._manifest = canonical
        self._prepared.clear()
        self._completed.clear()

    def prepare(self, entry: ManifestEntry) -> PreparedDownload:
        """Create the safe parent and exclusive temporary file before URL issuance."""
        staging, manifest = self._active()
        if entry not in manifest.entries:
            raise ManifestMismatchError("Prepared entry is not part of the active manifest.")
        final_path = staging.joinpath(*entry.relative_path.value.split("/"))
        _mkdir_private_parents(staging, final_path.parent)
        token = uuid4()
        part_path = final_path.parent / f".{final_path.name}.{token.hex}.part"
        descriptor = os.open(
            part_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        self._prepared[token] = _PreparedState(entry, descriptor, part_path, final_path)
        return PreparedDownload(token)

    def write(self, prepared: PreparedDownload, item: DownloadItem) -> None:
        """Verify one GET into its part file, then atomically place it within staging."""
        if not isinstance(prepared.token, UUID):
            raise ContentConflictError("Prepared download handle is invalid.")
        state = self._prepared.get(prepared.token)
        if state is None:
            raise ContentConflictError("Prepared download handle is not active.")
        if item.entry != state.entry:
            raise ManifestMismatchError("Download item differs from its prepared manifest entry.")
        try:
            self._transfer.download_to(item, state.descriptor)
            os.close(state.descriptor)
            state.descriptor = -1
            os.rename(state.part_path, state.final_path)
            self._completed.add(state.entry.relative_path.value)
        except Exception:
            if state.descriptor >= 0:
                os.close(state.descriptor)
                state.descriptor = -1
            state.part_path.unlink(missing_ok=True)
            raise
        finally:
            self._prepared.pop(prepared.token, None)

    def commit(self) -> Path:
        """Publish exactly one complete staged tree using the platform primitive."""
        staging, manifest = self._active()
        if self._prepared:
            raise ContentConflictError("Prepared downloads remain unfinished.")
        expected = {entry.relative_path.value for entry in manifest.entries}
        if self._completed != expected:
            raise ManifestMismatchError("Staged tree does not contain the complete manifest.")
        if self._output is None or self._requested_output is None:
            raise ContentConflictError("Tree output state is unavailable.")
        self._publisher(staging, self._output)
        self._staging = None
        return self._requested_output

    def abort(self) -> None:
        """Remove only the recorded generated staging sibling without following links."""
        for state in self._prepared.values():
            if state.descriptor >= 0:
                os.close(state.descriptor)
            state.part_path.unlink(missing_ok=True)
        self._prepared.clear()
        staging = self._staging
        parent = self._staging_parent
        if staging is None or parent is None:
            return
        if staging.parent.resolve(strict=True) != parent or not staging.name.startswith(
            ".robolake-"
        ):
            raise UnsafePathError("Refusing to clean an unrecognized staging directory.")
        try:
            staging_stat = os.lstat(staging)
        except FileNotFoundError:
            self._staging = None
            return
        if stat.S_ISLNK(staging_stat.st_mode) or not stat.S_ISDIR(staging_stat.st_mode):
            raise UnsafePathError("Refusing to clean an unsafe staging path.")
        shutil.rmtree(staging)
        self._staging = None

    def _active(self) -> tuple[Path, Manifest]:
        if self._staging is None or self._manifest is None:
            raise ContentConflictError("No tree download is active.")
        return self._staging, self._manifest


def _require_existing_non_symlink_directory(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            value = os.lstat(current)
        except FileNotFoundError as error:
            raise UnsafePathError("Output parent directory does not exist.") from error
        if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
            raise UnsafePathError("Output parent must contain no symlink components.")


def _mkdir_private_parents(staging: Path, target: Path) -> None:
    relative = target.relative_to(staging)
    current = staging
    for part in relative.parts:
        current /= part
        try:
            value = os.lstat(current)
        except FileNotFoundError:
            os.mkdir(current, mode=0o700)
            continue
        if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
            raise UnsafePathError("Staging path contains an unsafe parent component.")

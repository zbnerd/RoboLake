"""READY DatasetVersion to atomically published local tree workflow."""

from __future__ import annotations

from pathlib import Path

from robolake.application.contracts import PullResult
from robolake.application.ports import ControlPlanePort, TreeDownloaderPort
from robolake.domain.errors import IllegalTransitionError, ManifestMismatchError
from robolake.domain.identifiers import DatasetReference
from robolake.domain.lifecycle import VersionState


class PullWorkflow:
    """Materialize one capability at a time and publish only the complete tree."""

    def __init__(self, control_plane: ControlPlanePort, tree: TreeDownloaderPort) -> None:
        self._control_plane = control_plane
        self._tree = tree

    def run(self, reference: DatasetReference, output: Path) -> PullResult:
        version = self._control_plane.resolve(reference)
        if version.state is not VersionState.READY:
            raise IllegalTransitionError("Only READY DatasetVersions can be pulled.")
        manifest = self._control_plane.manifest(version.id)
        if manifest.sha256 != version.manifest_sha256:
            raise ManifestMismatchError("Stored manifest does not match the DatasetVersion.")
        if (
            manifest.file_count != version.file_count
            or manifest.logical_bytes != version.logical_bytes
            or manifest.unique_blob_count != version.unique_blob_count
            or manifest.unique_blob_bytes != version.unique_blob_bytes
        ):
            raise ManifestMismatchError("Stored manifest totals do not match the DatasetVersion.")

        attempted_begin = True
        try:
            self._tree.begin(output, manifest)
            cursor: str | None = None
            for ordinal, expected in enumerate(manifest.entries):
                prepared = self._tree.prepare(expected)
                capability = self._control_plane.download_plan(version.id, cursor)
                if capability.item is None or capability.item.entry != expected:
                    raise ManifestMismatchError(
                        "Download capability does not match the expected manifest ordinal."
                    )
                is_last = ordinal + 1 == manifest.file_count
                if capability.complete != is_last:
                    raise ManifestMismatchError("Download capability completion marker is invalid.")
                if is_last and capability.next_cursor is not None:
                    raise ManifestMismatchError("Final download capability has an extra cursor.")
                if not is_last and capability.next_cursor is None:
                    raise ManifestMismatchError("Download capability omitted the next cursor.")
                self._tree.write(prepared, capability.item)
                cursor = capability.next_cursor
            final_output = self._tree.commit()
        except KeyboardInterrupt:
            if attempted_begin:
                self._tree.abort()
            raise
        except Exception:
            if attempted_begin:
                self._tree.abort()
            raise
        return PullResult(
            reference=version.reference,
            output=final_output,
            materialized_file_count=manifest.file_count,
            materialized_bytes=manifest.logical_bytes,
        )

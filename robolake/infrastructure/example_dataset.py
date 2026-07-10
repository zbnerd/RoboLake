"""Deterministic synthetic dataset fixture generation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


class DestinationNotEmptyError(ValueError):
    """Raised when generation would overwrite an existing destination."""


@dataclass(frozen=True)
class GeneratedDataset:
    """Summary of a generated synthetic dataset tree."""

    root: Path
    relative_files: tuple[Path, ...]
    total_bytes: int


def _deterministic_bytes(*, seed: int, label: str, size: int) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < size:
        material = f"robolake-synthetic:{seed}:{label}:{counter}".encode()
        output.extend(hashlib.sha256(material).digest())
        counter += 1
    return bytes(output[:size])


def generate_synthetic_dataset(destination: Path, *, seed: int = 0) -> GeneratedDataset:
    """Create a small deterministic tree containing only invented data."""
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise DestinationNotEmptyError(f"destination is not an empty directory: {destination}")
    if destination.exists() and any(destination.iterdir()):
        raise DestinationNotEmptyError(f"destination is not empty: {destination}")

    imu_rows = ["timestamp_ms,axis_x,axis_y,axis_z"]
    for index in range(8):
        values = [((seed + 1) * (index + offset + 3)) % 101 for offset in range(3)]
        imu_rows.append(f"{index * 10},{values[0]},{values[1]},{values[2]}")

    files = {
        Path("README.txt"): (
            b"Synthetic RoboLake example dataset.\n"
            b"All bytes in this directory are generated and contain no real robot data.\n"
        ),
        Path("camera/front/frame-000001.bin"): _deterministic_bytes(
            seed=seed,
            label="camera-front-frame-000001",
            size=8192,
        ),
        Path("metadata/session.json"): (
            json.dumps(
                {"generator": "RoboLake", "seed": seed, "synthetic": True},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode(),
        Path("sensors/imu.csv"): ("\n".join(imu_rows) + "\n").encode(),
    }

    destination.mkdir(parents=True, exist_ok=True)
    for relative_path, content in files.items():
        output_path = destination / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content)

    relative_files = tuple(sorted(files, key=lambda path: path.as_posix()))
    return GeneratedDataset(
        root=destination,
        relative_files=relative_files,
        total_bytes=sum(len(content) for content in files.values()),
    )

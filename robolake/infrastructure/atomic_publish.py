"""Platform-specific atomic no-replace directory publication."""

from __future__ import annotations

import ctypes
import errno
import os
import sys
from pathlib import Path
from typing import Any

from robolake.domain.errors import OutputExistsError, UnsupportedFilesystemError

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_RENAME_EXCL = 0x00000004


def publish_no_replace(staging: Path, output: Path) -> None:
    """Atomically publish staging without any replacing/check-then-rename fallback."""
    library: Any = ctypes.CDLL(None, use_errno=True)
    source = os.fsencode(staging)
    destination = os.fsencode(output)
    if sys.platform == "linux":
        renameat2 = library.renameat2
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            _AT_FDCWD,
            source,
            _AT_FDCWD,
            destination,
            _RENAME_NOREPLACE,
        )
    elif sys.platform == "darwin":
        renamex_np = library.renamex_np
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(source, destination, _RENAME_EXCL)
    else:
        raise UnsupportedFilesystemError(
            "Atomic no-replace publication requires supported Linux/macOS primitives."
        )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise OutputExistsError("Output already exists; it was not replaced.")
    raise UnsupportedFilesystemError(
        "Filesystem does not support the required atomic no-replace publication."
    )

"""
Filesystem helpers that keep locally stored data (recordings, sessions,
settings, dictionary) readable only by the current user (mode 0600 / 0700).

Protects patient information on shared or lost Macs; combine with FileVault
for at-rest encryption.
"""
from __future__ import annotations

import os
from pathlib import Path


def secure_dir(path: Path) -> Path:
    """Create (if needed) a directory that only the owner can access."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def harden(path: Path) -> Path:
    """Restrict a file to owner-only read/write (mode 0600)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path

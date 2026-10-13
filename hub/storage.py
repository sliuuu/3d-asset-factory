"""Content-addressed blob store. Binaries are keyed by SHA-256 (Tech Spec §11)."""
import hashlib
import shutil
from pathlib import Path

from .config import get_settings


def _root() -> Path:
    root = get_settings().blob_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def path_for(sha256: str) -> Path:
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ValueError("invalid sha256")
    return _root() / sha256[:2] / sha256


def put_bytes(data: bytes) -> tuple[str, int]:
    sha = hashlib.sha256(data).hexdigest()
    dest = path_for(sha)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)
    return sha, len(data)


def put_file(src: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    with open(src, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    sha = h.hexdigest()
    dest = path_for(sha)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    return sha, dest.stat().st_size


def exists(sha256: str) -> bool:
    return path_for(sha256).exists()

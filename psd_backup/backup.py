"""Backup engine: change detection, copy + hash, verification, atomic promotion.

The backup folder only ever holds two files, whatever PSD is being watched:
  backup.current.<ext>   working copy, rebuilt whenever the original changes
  backup.official.<ext>  last verified copy; replaced only via atomic os.replace
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from . import psd_check
from .config import write_json_atomic

log = logging.getLogger(__name__)

CHUNK = 4 * 1024 * 1024
BACKUP_EXTS = (".psd", ".psb")

UNCHANGED = "unchanged"
BACKED_UP = "backed_up"
BUSY = "skipped_busy"
FAILED = "failed"


@dataclass
class Result:
    status: str
    message: str = ""


@dataclass
class State:
    source_path: str = ""        # original the official backup was made from
    official_hash: str = ""
    last_mtime: Optional[int] = None
    last_size: Optional[int] = None
    last_backup_at: str = ""

    @classmethod
    def load(cls, path: Path) -> "State":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path) -> None:
        write_json_atomic(path, asdict(self))


def norm(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(path))


def file_sig(path: Path) -> Tuple[int, int]:
    st = os.stat(path)
    return st.st_mtime_ns, st.st_size


def backup_paths(backup_dir: Path, ext: str) -> Tuple[Path, Path]:
    ext = ext.lower() if ext.lower() in BACKUP_EXTS else ".psd"
    return backup_dir / f"backup.current{ext}", backup_dir / f"backup.official{ext}"


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_with_hash(src: Path, dst: Path) -> str:
    """Stream src to dst, fsync, and return the SHA-256 of the bytes read."""
    h = hashlib.sha256()
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        for chunk in iter(lambda: fin.read(CHUNK), b""):
            h.update(chunk)
            fout.write(chunk)
        fout.flush()
        os.fsync(fout.fileno())
    return h.hexdigest()


def remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


class BackupEngine:
    def __init__(
        self,
        state_path: Path,
        stable_for: float = 6.0,
        poll: float = 2.0,
        stable_timeout: float = 60.0,
    ):
        self.state_path = state_path
        self.state = State.load(state_path)
        self.stable_for = stable_for
        self.poll = poll
        self.stable_timeout = stable_timeout
        self._lock = threading.Lock()

    def reset_detection(self) -> None:
        """Forget the last seen mtime/size so the next cycle re-examines the file."""
        with self._lock:
            self.state.last_mtime = None
            self.state.last_size = None
            self.state.save(self.state_path)

    def run_cycle(self, source: str, backup_dir: str, force: bool = False) -> Result:
        with self._lock:
            try:
                return self._run(Path(source), Path(backup_dir), force)
            except OSError as e:
                log.exception("Backup cycle failed")
                return Result(FAILED, f"{type(e).__name__}: {e}")

    def _wait_until_stable(self, src: Path) -> bool:
        """True once size/mtime stop changing for `stable_for` seconds and the file can be opened."""
        deadline = time.monotonic() + self.stable_timeout
        last = file_sig(src)
        stable_since = time.monotonic()
        while time.monotonic() - stable_since < self.stable_for:
            if time.monotonic() > deadline:
                return False
            time.sleep(self.poll)
            sig = file_sig(src)
            if sig != last:
                last, stable_since = sig, time.monotonic()
        try:
            with open(src, "rb"):
                pass
        except PermissionError:
            return False
        return True

    def _run(self, src: Path, bdir: Path, force: bool) -> Result:
        st = self.state
        if not src.is_file():
            return Result(FAILED, f"Original not found: {src}")
        bdir.mkdir(parents=True, exist_ok=True)
        current, official = backup_paths(bdir, src.suffix)
        if norm(src) in (norm(current), norm(official)):
            return Result(FAILED, "The original cannot be one of the backup files")

        same_source = st.source_path == norm(src)
        if not force and same_source and file_sig(src) == (st.last_mtime, st.last_size):
            return Result(UNCHANGED, "No changes")

        if not self._wait_until_stable(src):
            return Result(BUSY, "Original is still being saved; will retry")
        sig = file_sig(src)

        # 1. original -> current backup
        tmp = current.with_name(current.name + ".tmp")
        try:
            src_hash = copy_with_hash(src, tmp)
        except PermissionError:
            remove_quietly(tmp)
            return Result(BUSY, "Original is locked; will retry")
        os.replace(tmp, current)

        if file_sig(src) != sig:
            return Result(BUSY, "Original changed during copy; will retry")

        if same_source and src_hash == st.official_hash and official.exists():
            st.last_mtime, st.last_size = sig
            st.save(self.state_path)
            return Result(UNCHANGED, "Saved, but content is identical")

        # 2. verify current backup
        if hash_file(current) != src_hash:
            return Result(FAILED, "Current backup does not match the original (hash mismatch)")
        try:
            psd_check.validate(current)
        except psd_check.PSDError as e:
            return Result(FAILED, f"Original looks corrupt, official backup kept: {e}")

        # 3. promote current -> official atomically
        tmp = official.with_name(official.name + ".tmp")
        copy_with_hash(current, tmp)
        if hash_file(tmp) != src_hash:
            remove_quietly(tmp)
            return Result(FAILED, "Official copy failed verification; official backup kept")
        os.replace(tmp, official)

        # Keep only one backup pair: drop leftovers with the other extension.
        for ext in BACKUP_EXTS:
            if ext != official.suffix:
                for p in backup_paths(bdir, ext):
                    remove_quietly(p)

        st.source_path = norm(src)
        st.official_hash = src_hash
        st.last_mtime, st.last_size = sig
        st.last_backup_at = datetime.now().isoformat(timespec="seconds")
        st.save(self.state_path)
        return Result(BACKED_UP, f"Backed up {src.name}")

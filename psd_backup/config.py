"""Settings stored as JSON in %APPDATA%\\PSDBackup (or ~/.psdbackup off Windows)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from . import APP_NAME

DEFAULT_INTERVAL_SEC = 300


def app_dir() -> Path:
    base = os.environ.get("APPDATA")
    path = Path(base) / APP_NAME if base else Path.home() / ".psdbackup"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@dataclass
class Config:
    source: str = ""
    backup_dir: str = ""
    interval_sec: int = DEFAULT_INTERVAL_SEC
    autostart: bool = False

    @property
    def is_complete(self) -> bool:
        return bool(self.source and self.backup_dir)

    @classmethod
    def load(cls, path: Path) -> "Config":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in data.items() if k in known})
        cfg.interval_sec = max(10, int(cfg.interval_sec))
        return cfg

    def save(self, path: Path) -> None:
        write_json_atomic(path, asdict(self))

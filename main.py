"""PSD Backup entry point."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from psd_backup import APP_NAME
from psd_backup.backup import BackupEngine
from psd_backup.config import Config, app_dir


def setup_logging(path) -> None:
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handlers = [handler]
    if sys.stderr is not None:  # None in a --noconsole build
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers)


def acquire_single_instance():
    """Return a handle that keeps a named mutex alive, or None if already running."""
    if sys.platform != "win32":
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, f"Local\\{APP_NAME}SingleInstance")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return handle


def main() -> int:
    instance = acquire_single_instance()
    if not instance:
        return 0

    base = app_dir()
    log_path = base / "backup.log"
    setup_logging(log_path)

    from psd_backup import tray

    cfg_path = base / "settings.json"
    cfg = Config.load(cfg_path)
    if not cfg.is_complete:
        cfg.source = cfg.source or tray.ask_source() or ""
        if not cfg.source:
            return 0
        cfg.backup_dir = cfg.backup_dir or tray.ask_backup_dir() or ""
        if not cfg.backup_dir:
            return 0
        cfg.save(cfg_path)

    logging.info("Starting: watching %s -> %s every %ss", cfg.source, cfg.backup_dir, cfg.interval_sec)
    engine = BackupEngine(base / "state.json")
    tray.TrayApp(cfg, cfg_path, engine, log_path).run()
    logging.info("Stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

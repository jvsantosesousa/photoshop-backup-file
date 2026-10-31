"""Background thread that runs the backup job every N seconds."""
from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, interval: Callable[[], float], job: Callable[[bool], None]):
        self._interval = interval
        self._job = job
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._force = False
        self.paused = False
        self._thread = threading.Thread(target=self._loop, name="backup-scheduler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def trigger(self) -> None:
        """Run a forced cycle now, even when paused."""
        self._force = True
        self._wake.set()

    def reschedule(self) -> None:
        """Run a normal check now, then wait the (possibly new) interval."""
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            force, self._force = self._force, False
            if force or not self.paused:
                try:
                    self._job(force)
                except Exception:
                    log.exception("Backup job crashed")
            self._wake.wait(self._interval())
            self._wake.clear()

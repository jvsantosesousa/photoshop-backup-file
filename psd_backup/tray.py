"""System tray UI: icon, menu, dialogs and notifications."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pystray
from PIL import Image, ImageDraw, ImageFont

from . import APP_NAME, autostart
from .backup import BACKED_UP, BUSY, FAILED, UNCHANGED, BackupEngine, Result
from .config import Config
from .scheduler import Scheduler

log = logging.getLogger(__name__)

COLORS = {
    "ok": (46, 160, 67),
    "failed": (207, 34, 46),
    "paused": (130, 130, 130),
    "idle": (31, 111, 235),
}


def make_icon(color) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=12, fill=color)
    try:
        font = ImageFont.load_default(size=36)
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    d.text((32, 33), "B", fill="white", font=font, anchor="mm")
    return img


# --- tkinter dialogs (a fresh hidden root per dialog keeps things thread-safe enough) ---

def _tk_root():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    return root


def ask_source(initial: str = "") -> Optional[str]:
    from tkinter import filedialog

    root = _tk_root()
    try:
        path = filedialog.askopenfilename(
            parent=root,
            title="Choose the PSD you are working on",
            initialdir=os.path.dirname(initial) or None,
            filetypes=[("Photoshop files", "*.psd *.psb"), ("All files", "*.*")],
        )
    finally:
        root.destroy()
    return path or None


def ask_backup_dir(initial: str = "") -> Optional[str]:
    from tkinter import filedialog

    root = _tk_root()
    try:
        path = filedialog.askdirectory(
            parent=root, title="Choose the backup folder", initialdir=initial or None
        )
    finally:
        root.destroy()
    return path or None


def confirm(title: str, message: str) -> bool:
    from tkinter import messagebox

    root = _tk_root()
    try:
        return messagebox.askyesno(title, message, parent=root)
    finally:
        root.destroy()


def open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


class TrayApp:
    def __init__(self, cfg: Config, cfg_path: Path, engine: BackupEngine, log_path: Path):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.engine = engine
        self.log_path = log_path
        self.last: Optional[Result] = None
        self.last_at: Optional[datetime] = None
        self._failing = False
        self.scheduler = Scheduler(lambda: self.cfg.interval_sec, self._job)
        self.icon = pystray.Icon(APP_NAME, make_icon(COLORS["idle"]), APP_NAME, self._menu())

    # --- status ---

    def status_text(self, _item=None) -> str:
        if self.scheduler.paused:
            return "Paused"
        if not self.last:
            return "Starting..."
        when = self.last_at.strftime("%H:%M") if self.last_at else ""
        st = self.engine.state
        if self.last.status == FAILED:
            return f"Failed {when}: {self.last.message}"[:120]
        if self.last.status == BUSY:
            return f"Waiting for save to finish ({when})"
        if st.last_backup_at:
            backed = datetime.fromisoformat(st.last_backup_at).strftime("%d/%m %H:%M")
            return f"Last backup {backed} ✓ (checked {when})"
        return f"Checked {when}"

    def backup_of_text(self, _item=None) -> str:
        src = self.engine.state.source_path
        return f"Backup contains: {os.path.basename(src)}" if src else "No backup yet"

    def _refresh(self) -> None:
        if self.scheduler.paused:
            color = COLORS["paused"]
        elif self.last and self.last.status == FAILED:
            color = COLORS["failed"]
        elif self.engine.state.last_backup_at:
            color = COLORS["ok"]
        else:
            color = COLORS["idle"]
        self.icon.icon = make_icon(color)
        self.icon.title = f"{APP_NAME} - {self.status_text()}"[:127]
        self.icon.update_menu()

    def _notify(self, message: str) -> None:
        try:
            self.icon.notify(message, APP_NAME)
        except Exception:
            log.debug("Notification not supported", exc_info=True)

    # --- job ---

    def _job(self, force: bool) -> None:
        result = self.engine.run_cycle(self.cfg.source, self.cfg.backup_dir, force=force)
        self.last, self.last_at = result, datetime.now()
        level = logging.WARNING if result.status == FAILED else logging.INFO
        if result.status != UNCHANGED or force:
            log.log(level, "%s: %s", result.status, result.message)

        if result.status == FAILED and not self._failing:
            self._notify(f"Backup failed: {result.message}")
        elif result.status == BACKED_UP and (force or self._failing):
            self._notify(result.message)
        elif force:
            self._notify(result.message)
        self._failing = result.status == FAILED
        self._refresh()

    # --- menu actions ---

    def _menu(self) -> pystray.Menu:
        item = pystray.MenuItem
        return pystray.Menu(
            item(self.status_text, None, enabled=False),
            item(self.backup_of_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            item("Backup now", self.on_backup_now, default=True),
            item(lambda _: "Resume" if self.scheduler.paused else "Pause", self.on_pause),
            pystray.Menu.SEPARATOR,
            item("Change PSD...", self.on_change_source),
            item("Change backup folder...", self.on_change_backup_dir),
            item("Open backup folder", lambda: open_path(Path(self.cfg.backup_dir))),
            item("Open log", lambda: open_path(self.log_path)),
            item(
                "Start with Windows",
                self.on_toggle_autostart,
                checked=lambda _: self.cfg.autostart,
                visible=sys.platform == "win32",
            ),
            pystray.Menu.SEPARATOR,
            item("Quit", self.on_quit),
        )

    def on_backup_now(self) -> None:
        self.scheduler.trigger()

    def on_pause(self) -> None:
        self.scheduler.paused = not self.scheduler.paused
        log.info("Paused" if self.scheduler.paused else "Resumed")
        self._refresh()

    def on_change_source(self) -> None:
        path = ask_source(self.cfg.source)
        if not path or os.path.normcase(path) == os.path.normcase(self.cfg.source):
            return
        old = os.path.basename(self.engine.state.source_path or self.cfg.source)
        if old and not confirm(
            APP_NAME,
            f"This will replace the current backup of\n\n{old}\n\nwith\n\n"
            f"{os.path.basename(path)}\n\nContinue?",
        ):
            return
        log.info("Switching watched PSD to %s", path)
        self.cfg.source = path
        self.cfg.save(self.cfg_path)
        self.engine.reset_detection()
        self.scheduler.trigger()

    def on_change_backup_dir(self) -> None:
        path = ask_backup_dir(self.cfg.backup_dir)
        if not path:
            return
        log.info("Backup folder changed to %s", path)
        self.cfg.backup_dir = path
        self.cfg.save(self.cfg_path)
        self.engine.reset_detection()
        self.scheduler.trigger()

    def on_toggle_autostart(self) -> None:
        try:
            autostart.set_enabled(not self.cfg.autostart)
        except OSError:
            log.exception("Could not change autostart")
            self._notify("Could not change 'Start with Windows'")
            return
        self.cfg.autostart = not self.cfg.autostart
        self.cfg.save(self.cfg_path)
        self._refresh()

    def on_quit(self) -> None:
        self.scheduler.stop()
        self.icon.stop()

    def run(self) -> None:
        def setup(icon):
            icon.visible = True
            self.scheduler.start()

        self.icon.run(setup=setup)

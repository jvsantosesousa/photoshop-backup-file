"""System tray UI: icon, menu, dialogs and notifications."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
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


# --- dialogs ---
# Windows: tkinter, always called from a dedicated thread with COM initialized as
# STA (the Explorer search box in the Open dialog needs it). macOS: osascript,
# since Tk can't run off the main thread there.

@contextmanager
def com_sta():
    if sys.platform != "win32":
        yield
        return
    import ctypes

    hr = ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    try:
        yield
    finally:
        if hr >= 0:
            ctypes.windll.ole32.CoUninitialize()


def _osascript(lines, *args) -> Optional[str]:
    """Run an AppleScript `on run argv` body; None if the user cancelled."""
    cmd = ["osascript", "-e", "on run argv", *sum((["-e", l] for l in lines), []), "-e", "end run", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _tk_root():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    root.update()
    root.lift()
    root.focus_force()
    return root


def ask_source(initial: str = "") -> Optional[str]:
    if sys.platform == "darwin":
        return _osascript(
            ['POSIX path of (choose file with prompt "Choose the PSD you are working on" '
             'of type {"com.adobe.photoshop-image", "psd", "psb"})']
        ) or None

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
    if sys.platform == "darwin":
        path = _osascript(['POSIX path of (choose folder with prompt "Choose the backup folder")'])
        return path.rstrip("/") if path else None

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
    if sys.platform == "darwin":
        return _osascript(
            ['display dialog (item 2 of argv) with title (item 1 of argv) '
             'buttons {"Cancel", "Continue"} default button "Continue"'],
            title,
            message,
        ) is not None

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
        self._dialog_open = False
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

    def _run_dialog(self, flow) -> None:
        """Run a dialog flow on its own thread so the tray's message loop never blocks."""
        if self._dialog_open:
            return
        self._dialog_open = True

        def worker():
            try:
                with com_sta():
                    flow()
            except Exception:
                log.exception("Dialog failed")
            finally:
                self._dialog_open = False

        threading.Thread(target=worker, name="dialog", daemon=True).start()

    def on_change_source(self) -> None:
        self._run_dialog(self._change_source)

    def on_change_backup_dir(self) -> None:
        self._run_dialog(self._change_backup_dir)

    def _change_source(self) -> None:
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

    def _change_backup_dir(self) -> None:
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

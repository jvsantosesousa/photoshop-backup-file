import sys
import threading

import pytest

from psd_backup import tray
from psd_backup.backup import BackupEngine
from psd_backup.config import Config

from .test_psd_check import make_psd


@pytest.fixture
def app(tmp_path):
    src = make_psd(tmp_path / "art.psd")
    cfg = Config(source=str(src), backup_dir=str(tmp_path / "backups"))
    engine = BackupEngine(tmp_path / "state.json", stable_for=0, poll=0)
    a = tray.TrayApp(cfg, tmp_path / "settings.json", engine, tmp_path / "backup.log")
    a.notes = []
    a._notify = a.notes.append
    a._refresh = lambda: None
    return a


@pytest.mark.skipif(sys.platform != "win32", reason="Tk dialogs are only used on Windows")
def test_dialog_root_is_topmost():
    # A non-topmost root let the confirm dialog open behind other windows.
    root = tray._tk_root()
    try:
        assert root.attributes("-topmost")
    finally:
        root.destroy()


def test_click_while_dialog_open_tells_the_user(app):
    release = threading.Event()
    started = threading.Event()

    def flow():
        started.set()
        release.wait(5)

    app._run_dialog(flow)
    assert started.wait(2)
    app._run_dialog(lambda: pytest.fail("second dialog must not open"))
    assert app.notes == ["A PSD Backup window is already open"]
    release.set()


def test_failed_dialog_does_not_block_the_menu(app):
    done = threading.Event()

    def boom():
        done.set()
        raise RuntimeError("dialog crashed")

    app._run_dialog(boom)
    assert done.wait(2)
    for _ in range(100):
        if not app._dialog_open:
            break
        threading.Event().wait(0.02)
    assert not app._dialog_open


def test_backup_now_always_reports_a_result(app):
    app._job(force=True)
    assert app.notes == ["Backed up art.psd"]
    app._job(force=True)  # nothing changed: still tell the user
    assert app.notes[-1] == "Saved, but content is identical"


def test_scheduled_check_without_changes_is_silent(app):
    app._job(force=False)
    app.notes.clear()
    app._job(force=False)
    assert app.notes == []

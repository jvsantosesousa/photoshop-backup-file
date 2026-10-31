"""Windows UI smoke test for the built PSDBackup.exe (runs in CI on windows-latest).

Drives the real app the way a person would:
  tray menu -> Change PSD... -> click the Explorer search box, type a name,
  pick the result, confirm; then the same for Change backup folder...; then
  Check every -> 20 minutes; then Quit.
While each dialog is open it checks that neither the tray nor the dialog is hung.

Usage: python ci/ui_smoke.py dist\\PSDBackup.exe
On failure, screenshots, control trees and the app log go to ./ui-artifacts.
"""
import ctypes
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import traceback
from ctypes import wintypes
from pathlib import Path

from pywinauto import Desktop
from pywinauto.keyboard import send_keys

user32 = ctypes.windll.user32
WM_NULL = 0x0000
WM_RBUTTONUP = 0x0205
WM_PYSTRAY_NOTIFY = 0x400 + 11  # pystray's WM_NOTIFY (WM_USER + 11)
SMTO_ABORTIFHUNG = 0x0002
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

ART = Path("ui-artifacts").resolve()
ART.mkdir(exist_ok=True)
uia = Desktop(backend="uia")
w32 = Desktop(backend="win32")
step = "setup"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def make_psd(path, fill=0, w=4, h=3, channels=3):
    data = struct.pack(">4sH6sHIIHH", b"8BPS", 1, b"\0" * 6, channels, h, w, 8, 3)
    data += struct.pack(">IIIH", 0, 0, 0, 0) + bytes([fill]) * (channels * w * h)
    path.write_bytes(data)


def wait_for(pred, timeout, what):
    end = time.time() + timeout
    last_err = None
    while time.time() < end:
        try:
            value = pred()
            if value:
                return value
        except Exception as e:  # element not there yet
            last_err = e
        time.sleep(0.25)
    raise AssertionError(f"Timed out after {timeout}s waiting for {what}" + (f" ({last_err!r})" if last_err else ""))


def tray_hwnds():
    found = []

    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value.startswith("PSDBackup") and buf.value.endswith("SystemTrayIcon"):
            found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def responsive(hwnd, timeout_ms=3000):
    result = ctypes.c_size_t()
    return bool(user32.SendMessageTimeoutW(hwnd, WM_NULL, 0, 0, SMTO_ABORTIFHUNG, timeout_ms, ctypes.byref(result)))


def assert_not_frozen(dlg):
    for h in tray_hwnds():
        assert responsive(h), "tray icon thread is not responding while the dialog is open"
    assert not user32.IsHungAppWindow(dlg.handle), "dialog window is hung"
    assert responsive(dlg.handle), "dialog window is not responding"


def find_menu_item(label):
    # Read items straight from the popup's HMENU (win32); UIA sometimes reports
    # an open popup menu with no children, so it is only the fallback.
    for menu in w32.windows(class_name="#32768"):
        try:
            for mi in menu.menu().items():
                if mi.text().replace("&", "") == label:
                    return mi
        except Exception:
            pass
    for menu in uia.windows(control_type="Menu"):
        for mi in menu.descendants(control_type="MenuItem"):
            if mi.window_text() == label:
                return mi
    return None


def click_tray_menu(*path):
    """Open the tray menu exactly like a right-click does, then click through `path`."""
    for h in tray_hwnds():
        user32.PostMessageW(h, WM_PYSTRAY_NOTIFY, 0, WM_RBUTTONUP)
    for label in path:
        wait_for(lambda: find_menu_item(label), 10, f"menu item {label!r}").click_input()
        time.sleep(0.5)


def find_dialog(title):
    return wait_for(lambda: uia.window(title=title).wrapper_object(), 15, f"dialog {title!r}")


def search_in_dialog(dlg, text):
    """Click the Explorer search box, type `text`, and return the result list items."""
    box = wait_for(
        lambda: next(
            e for e in dlg.descendants(control_type="Edit")
            if e.automation_id() == "SearchEditBox" or e.window_text().lower().startswith("search")
        ),
        15,
        "Explorer search box",
    )
    log(f"search box found: name={box.window_text()!r} id={box.automation_id()!r}")
    before = [i.window_text() for i in dlg.descendants(control_type="ListItem")]
    log(f"folder listing before search: {before}")
    box.click_input()
    wait_for(lambda: box.has_keyboard_focus(), 5, "keyboard focus in the search box")
    log("search box has keyboard focus")
    assert_not_frozen(dlg)
    send_keys(text, pause=0.05)
    wait_for(lambda: text in (box.get_value() or ""), 5, f"typed text {text!r} to appear in the search box")
    log(f"typed {text!r} into search box")
    send_keys("{ENTER}")
    assert_not_frozen(dlg)

    def results():
        # Only accept a list the search actually filtered: every item must match.
        names = [i.window_text() for i in dlg.descendants(control_type="ListItem")]
        return names if names and all(n.startswith(text) for n in names) else None

    names = wait_for(results, 90, f"search results filtered down to {text!r}")
    log(f"search results: {names}")
    assert_not_frozen(dlg)
    return next(i for i in dlg.descendants(control_type="ListItem") if i.window_text().startswith(text))


def dump(name):
    try:
        from PIL import ImageGrab

        ImageGrab.grab().save(ART / f"{name}.png")
    except Exception as e:
        log(f"screenshot failed: {e!r}")
    try:
        with open(ART / f"{name}-windows.txt", "w", encoding="utf-8") as f:
            for w in uia.windows():
                f.write(f"== {w.window_text()!r} {w.element_info.class_name}\n")
                for d in w.descendants()[:400]:
                    f.write(f"   {d.element_info.control_type} {d.window_text()!r} id={d.automation_id()!r}\n")
    except Exception as e:
        log(f"control dump failed: {e!r}")


def main(exe):
    global step
    root = Path(tempfile.mkdtemp(prefix="psdbackup-ui-"))
    work = root / "work"
    backups = root / "backups"
    appdata = root / "appdata"
    for d in (work, backups, appdata / "PSDBackup"):
        d.mkdir(parents=True)
    make_psd(work / "beta_start.psd", fill=1)
    make_psd(work / "alpha_target.psd", fill=2)
    for i in range(30):  # some clutter so search actually has to filter
        make_psd(work / f"other_{i:02}.psd", fill=3)
    (backups / "zz_new_target").mkdir()
    for i in range(10):
        (backups / f"folder_{i:02}").mkdir()

    cfg_path = appdata / "PSDBackup" / "settings.json"
    cfg_path.write_text(json.dumps({"source": str(work / "beta_start.psd"), "backup_dir": str(backups), "interval_sec": 300}))
    log_path = appdata / "PSDBackup" / "backup.log"
    cfg = lambda: json.loads(cfg_path.read_text())

    proc = subprocess.Popen([exe], env={**os.environ, "APPDATA": str(appdata)})
    try:
        step = "startup"
        wait_for(tray_hwnds, 30, "tray icon window")
        wait_for(lambda: (backups / "backup.official.psd").exists(), 30, "first backup")
        log("app started and made the first backup")

        step = "change-psd"
        click_tray_menu("Change PSD...")
        dlg = find_dialog("Choose the PSD you are working on")
        assert_not_frozen(dlg)
        item = search_in_dialog(dlg, "alpha_target")
        item.double_click_input()
        confirm = wait_for(lambda: uia.window(title="PSDBackup").child_window(title="Yes", control_type="Button").wrapper_object(), 15, "confirmation dialog")
        confirm.click_input()
        wait_for(lambda: cfg()["source"].endswith("alpha_target.psd"), 15, "settings to point at alpha_target.psd")
        wait_for(lambda: (backups / "backup.official.psd").read_bytes() == (work / "alpha_target.psd").read_bytes(), 30, "backup of alpha_target.psd")
        log("PASS: Change PSD via search")

        step = "change-folder"
        click_tray_menu("Change backup folder...")
        dlg = find_dialog("Choose the backup folder")
        assert_not_frozen(dlg)
        item = search_in_dialog(dlg, "zz_new_target")
        item.click_input()
        next(b for b in dlg.descendants(control_type="Button") if b.window_text() == "Select Folder").click_input()
        wait_for(lambda: cfg()["backup_dir"].rstrip("\\/").endswith("zz_new_target"), 15, "settings to point at zz_new_target")
        wait_for(lambda: (backups / "zz_new_target" / "backup.official.psd").exists(), 30, "backup in the new folder")
        log("PASS: Change backup folder via search")

        step = "interval"
        click_tray_menu("Check every", "20 minutes")
        wait_for(lambda: cfg()["interval_sec"] == 1200, 10, "interval_sec == 1200")
        log("PASS: Check every -> 20 minutes")

        step = "quit"
        click_tray_menu("Quit")
        proc.wait(15)
        log("PASS: Quit")
        return 0
    except Exception:
        traceback.print_exc()
        log(f"FAILED during step: {step}")
        dump(f"failure-{step}")
        return 1
    finally:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        if log_path.exists():
            (ART / "backup.log").write_text(log_path.read_text(encoding="utf-8"), encoding="utf-8")
            print("---- app log ----\n" + log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

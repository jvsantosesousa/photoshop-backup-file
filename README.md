# PSD Backup

A small Windows tray app that keeps a verified backup of the Photoshop file you're working on.

- **Original**: the PSD you edit.
- **`backup.current.psd`**: a working copy, rebuilt whenever the original changes.
- **`backup.official.psd`**: the last verified copy. It is replaced only after the current copy checks out.

Every 3 minutes the app checks the original. If it changed, the app:

1. Waits until Photoshop has finished saving.
2. Copies the original to the current backup.
3. Checks the copy: the SHA-256 must match and the PSD structure must be sound.
4. Only then replaces the official backup, using an atomic swap.

A half-saved or corrupt file never overwrites the official backup.

The backup folder only ever holds these two files. When you switch to another PSD, its backup overwrites them (you are asked to confirm first).

## Use

Run `PSDBackup.exe`. The first time, it asks for the PSD and the backup folder. Then it lives in the tray:

| Icon | Meaning |
|------|---------|
| Green | Backed up |
| Blue | No backup yet |
| Red | Last attempt failed |
| Grey | Paused |

The menu has: Backup now, Pause/Resume, Change PSD…, Change backup folder…, Open backup folder, Open log, Start with Windows, and Quit.

Settings, state and the log are stored in `%APPDATA%\PSDBackup\`. To change the check interval, edit `interval_sec` in `settings.json`.

## Build the .exe (must be done on Windows)

```bat
build.bat
```

This produces `dist\PSDBackup.exe`. You can also push to GitHub: the `Build Windows exe` workflow builds it and attaches it as a downloadable artifact.

## Tests

```sh
pip install pytest Pillow
python -m pytest
```

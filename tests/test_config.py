import json

from psd_backup.config import Config


def test_old_25_minute_setting_becomes_20(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"source": "a.psd", "backup_dir": "b", "interval_sec": 1500}))
    assert Config.load(p).interval_sec == 1200


def test_custom_interval_kept_and_minimum_enforced(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"interval_sec": 120}))
    assert Config.load(p).interval_sec == 120
    p.write_text(json.dumps({"interval_sec": 1}))
    assert Config.load(p).interval_sec == 10

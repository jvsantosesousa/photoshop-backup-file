import os

import pytest

from psd_backup import backup
from psd_backup.backup import BACKED_UP, BUSY, FAILED, UNCHANGED, BackupEngine

from .test_psd_check import make_psd


@pytest.fixture
def env(tmp_path):
    src_dir = tmp_path / "work"
    src_dir.mkdir()
    bdir = tmp_path / "backups"
    engine = BackupEngine(tmp_path / "state.json", stable_for=0, poll=0)
    return src_dir, bdir, engine


def touch_later(path):
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def files(d):
    return sorted(p.name for p in d.iterdir())


def test_first_backup_then_unchanged(env):
    src_dir, bdir, engine = env
    src = make_psd(src_dir / "art.psd")
    r = engine.run_cycle(str(src), str(bdir))
    assert r.status == BACKED_UP
    assert files(bdir) == ["backup.current.psd", "backup.official.psd"]
    assert (bdir / "backup.official.psd").read_bytes() == src.read_bytes()
    assert engine.run_cycle(str(src), str(bdir)).status == UNCHANGED


def test_modified_file_is_promoted(env):
    src_dir, bdir, engine = env
    src = make_psd(src_dir / "art.psd")
    engine.run_cycle(str(src), str(bdir))
    make_psd(src, fill=7)
    touch_later(src)
    assert engine.run_cycle(str(src), str(bdir)).status == BACKED_UP
    assert (bdir / "backup.official.psd").read_bytes() == src.read_bytes()


def test_corrupt_source_keeps_official(env):
    src_dir, bdir, engine = env
    src = make_psd(src_dir / "art.psd")
    engine.run_cycle(str(src), str(bdir))
    good = (bdir / "backup.official.psd").read_bytes()
    src.write_bytes(src.read_bytes()[:20])
    touch_later(src)
    r = engine.run_cycle(str(src), str(bdir))
    assert r.status == FAILED
    assert (bdir / "backup.official.psd").read_bytes() == good


def test_change_during_copy_is_not_promoted(env, monkeypatch):
    src_dir, bdir, engine = env
    src = make_psd(src_dir / "art.psd")
    real_copy = backup.copy_with_hash

    def copy_then_modify(s, d):
        h = real_copy(s, d)
        if s == src:
            make_psd(src, fill=9)
            touch_later(src)
        return h

    monkeypatch.setattr(backup, "copy_with_hash", copy_then_modify)
    assert engine.run_cycle(str(src), str(bdir)).status == BUSY
    assert not (bdir / "backup.official.psd").exists()


def test_same_content_new_mtime_does_not_rewrite_official(env):
    src_dir, bdir, engine = env
    src = make_psd(src_dir / "art.psd")
    engine.run_cycle(str(src), str(bdir))
    official = bdir / "backup.official.psd"
    before = os.stat(official).st_mtime_ns
    touch_later(src)
    r = engine.run_cycle(str(src), str(bdir))
    assert r.status == UNCHANGED and "identical" in r.message
    assert os.stat(official).st_mtime_ns == before


def test_switching_source_overwrites_single_pair(env):
    src_dir, bdir, engine = env
    a = make_psd(src_dir / "a.psd", fill=1)
    b = make_psd(src_dir / "b.psd", fill=2)
    engine.run_cycle(str(a), str(bdir))
    engine.reset_detection()
    assert engine.run_cycle(str(b), str(bdir)).status == BACKED_UP
    assert files(bdir) == ["backup.current.psd", "backup.official.psd"]
    assert (bdir / "backup.official.psd").read_bytes() == b.read_bytes()
    assert engine.state.source_path.endswith("b.psd")


def test_switching_psd_to_psb_leaves_no_leftovers(env):
    src_dir, bdir, engine = env
    a = make_psd(src_dir / "a.psd")
    b = make_psd(src_dir / "b.psb", version=2)
    engine.run_cycle(str(a), str(bdir))
    engine.reset_detection()
    assert engine.run_cycle(str(b), str(bdir)).status == BACKED_UP
    assert files(bdir) == ["backup.current.psb", "backup.official.psb"]


def test_switching_to_corrupt_source_keeps_old_official(env):
    src_dir, bdir, engine = env
    a = make_psd(src_dir / "a.psd")
    b = src_dir / "b.psd"
    b.write_bytes(b"not a psd at all, definitely not")
    engine.run_cycle(str(a), str(bdir))
    engine.reset_detection()
    assert engine.run_cycle(str(b), str(bdir)).status == FAILED
    assert (bdir / "backup.official.psd").read_bytes() == a.read_bytes()
    assert engine.state.source_path.endswith("a.psd")


def test_missing_source(env):
    src_dir, bdir, engine = env
    r = engine.run_cycle(str(src_dir / "nope.psd"), str(bdir))
    assert r.status == FAILED

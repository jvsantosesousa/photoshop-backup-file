import threading
import time

from psd_backup.scheduler import Scheduler


def test_reschedule_applies_new_interval_immediately():
    interval = [100.0]
    runs = []
    ran = threading.Event()

    def job(force):
        runs.append(force)
        ran.set()

    s = Scheduler(lambda: interval[0], job)
    s.start()
    assert ran.wait(2)  # first check runs on start
    ran.clear()

    interval[0] = 0.05
    s.reschedule()
    assert ran.wait(2)  # wakes now instead of after 100 s
    time.sleep(0.3)
    s.stop()
    assert len(runs) >= 3  # keeps running at the new, shorter interval
    assert not any(runs)  # reschedule never forces a backup

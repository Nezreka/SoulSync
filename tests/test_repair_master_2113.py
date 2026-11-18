"""#2113: the tools page's "Scheduled jobs" read its own saved copy of the
switch, while the automation engine gates scheduled jobs on its music
master (the automations page toggle). turned on over there, tools still said
"disabled" and the jobs ran. both now read the one switch."""

from core.repair_worker import RepairWorker


class _Db:
    def __init__(self, value):
        self.meta = {'automation_master_music_enabled': value}

    def get_metadata(self, key):
        return self.meta.get(key)

    def set_metadata(self, key, value):
        self.meta[key] = value


def _worker(value, saved):
    w = RepairWorker(database=None)
    w.db = _Db(value)
    w.enabled = saved
    return w


def test_tools_shows_the_switch_the_scheduler_reads():
    w = _worker('1', saved=False)  # automations turned it on
    assert w._sync_master() is True
    assert w.enabled is True
    w.db.meta['automation_master_music_enabled'] = '0'
    assert w._sync_master() is False


def test_toggle_flips_the_live_switch_not_the_stale_copy():
    w = _worker('1', saved=False)
    assert w.toggle() is False
    assert w.db.meta['automation_master_music_enabled'] == '0'


def test_no_engine_value_keeps_the_saved_state():
    w = _worker(None, saved=False)
    assert w._sync_master() is False
    w = RepairWorker(database=None)  # no db at all
    w.enabled = True
    assert w._sync_master() is True

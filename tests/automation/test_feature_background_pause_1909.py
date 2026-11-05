"""#1909: "if feasible, fully disabled modules could also stop their
unnecessary background processes." hiding is per profile, jobs are per
install, so podcasts and audiobooks only stop when an admin pauses them AND
every profile has them hidden (or can't reach them). a profile turning the
part back on resumes it with no second step. manual run now still runs.
"""

import json
from unittest.mock import MagicMock

from core import ui_features as uf
from core.automation_engine import AutomationEngine


class _Db:
    """metadata kv + a profile list, the two things the pause reads."""

    def __init__(self, profiles, off=None, paused=None, auto=None):
        self.rows = {}
        self.profiles = profiles
        for pid, feats in (off or {}).items():
            self.rows[f'ui_features_off:{pid}'] = json.dumps(feats)
        if paused is not None:
            self.rows['ui_features_paused'] = json.dumps(paused)
        self.auto = auto

    def get_metadata(self, k):
        return self.rows.get(k)

    def set_metadata(self, k, v):
        self.rows[k] = v

    def get_all_profiles(self):
        return self.profiles

    def get_automation(self, _id):
        return self.auto


ADMIN = {'id': 1, 'is_admin': True, 'allowed_pages': None}
KID = {'id': 3, 'is_admin': False, 'allowed_pages': None}
PODCAST_AUTO = {'id': 7, 'name': 'Podcast check', 'enabled': True,
                'action_type': 'scan_watchlist_podcasts', 'action_config': '{}',
                'trigger_type': 'schedule', 'trigger_config': '{"interval": 1}',
                'profile_id': 1}


# ── the rules ────────────────────────────────────────────────────────────────

def test_which_part_an_automation_belongs_to():
    assert uf.feature_for_automation(PODCAST_AUTO) == 'podcasts'
    assert uf.feature_for_automation({'action_type': 'audiobook_scan_library'}) == 'audiobooks'
    assert uf.feature_for_automation({'trigger_type': 'audiobook_download_complete'}) == 'audiobooks'
    assert uf.feature_for_automation({'action_type': 'process_wishlist'}) is None
    assert uf.feature_for_automation(None) is None


def test_only_when_every_profile_hid_it():
    db = _Db([ADMIN, KID], off={1: ['podcasts']}, paused=['podcasts'])
    assert not uf.everyone_hid(db, 'podcasts')
    assert not uf.background_paused(db, 'podcasts')
    db.rows['ui_features_off:3'] = json.dumps(['podcasts'])
    assert uf.everyone_hid(db, 'podcasts')
    assert uf.background_paused(db, 'podcasts')


def test_a_profile_that_cannot_open_it_counts_as_hiding_it():
    kid = {'id': 3, 'is_admin': False, 'allowed_pages': ['search', 'library']}
    db = _Db([ADMIN, kid], off={1: ['audiobooks']})
    assert uf.everyone_hid(db, 'audiobooks')


def test_the_admin_has_to_ask():
    db = _Db([ADMIN], off={1: ['podcasts']})
    assert uf.everyone_hid(db, 'podcasts')
    assert not uf.background_paused(db, 'podcasts')


def test_unreadable_profiles_mean_in_use():
    """get_all_profiles answers a bare admin row on a db error. that must
    not read as 'everyone hid it' and stop a household's podcasts."""
    db = _Db([{'id': 1, 'name': 'Admin', 'is_admin': True}], off={1: ['podcasts']},
             paused=['podcasts'])
    assert not uf.background_paused(db, 'podcasts')
    db.get_all_profiles = MagicMock(side_effect=RuntimeError('locked'))
    assert not uf.background_paused(db, 'podcasts')


def test_pause_saves_and_refuses_what_it_cannot_pause():
    db = _Db([ADMIN])
    assert uf.set_paused(db, 'podcasts', True) == ['podcasts']
    assert uf.set_paused(db, 'audiobooks', True) == ['podcasts', 'audiobooks']
    assert uf.set_paused(db, 'podcasts', False) == ['audiobooks']
    import pytest
    with pytest.raises(ValueError):
        uf.set_paused(db, 'video', True)


# ── through the real engine ──────────────────────────────────────────────────

def _engine(db):
    eng = AutomationEngine(db)
    eng._running = True
    eng.schedule_automation = MagicMock()
    db.update_automation_run = MagicMock(return_value=True)
    calls = []
    eng._action_handlers[PODCAST_AUTO['action_type']] = {
        'handler': lambda config: calls.append(config) or {'status': 'completed'},
        'guard': None,
    }
    return eng, calls


def test_a_paused_hidden_part_skips_its_scheduled_runs():
    db = _Db([ADMIN, KID], off={1: ['podcasts'], 3: ['podcasts']}, paused=['podcasts'],
             auto=PODCAST_AUTO)
    eng, calls = _engine(db)
    eng.run_automation(7, skip_delay=False)
    assert calls == []


def test_turning_it_back_on_for_one_profile_resumes_it():
    db = _Db([ADMIN, KID], off={1: ['podcasts']}, paused=['podcasts'], auto=PODCAST_AUTO)
    eng, calls = _engine(db)
    eng.run_automation(7, skip_delay=False)
    assert len(calls) == 1


def test_run_now_still_runs():
    db = _Db([ADMIN], off={1: ['podcasts']}, paused=['podcasts'], auto=PODCAST_AUTO)
    eng, calls = _engine(db)
    eng.run_automation(7, skip_delay=True)
    assert len(calls) == 1


def test_event_runs_are_dropped_while_paused():
    db = _Db([ADMIN], off={1: ['podcasts']}, paused=['podcasts'], auto=PODCAST_AUTO)
    eng, calls = _engine(db)
    eng._run_event_automation(PODCAST_AUTO, 7, {})
    assert calls == []

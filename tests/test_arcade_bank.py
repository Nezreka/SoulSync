"""Arcade P10 — the play-money bank and the slot machine.

Deliberately modest in what it claims. The balance is local and per profile,
so it is NOT authoritative for anything between players: a number only your
own machine can see cannot back a bet against somebody else. It exists so the
solo games have stakes, where the only person you could defraud is yourself.

What the bank does enforce is the one rule it can — you cannot stake what you
do not have — plus a bounded adjustment per call so a typo or a bug cannot
mint a fortune in one request.

Hermetic: a tmp MusicDatabase, no network.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def mdb(tmp_path):
    from database.music_database import MusicDatabase
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


class TestBank:
    def test_a_new_profile_starts_on_the_allowance(self, mdb):
        bank = mdb.get_arcade_bank(1)
        assert bank["balance"] == mdb.ARCADE_DAILY
        assert bank["allowance"] == mdb.ARCADE_DAILY
        assert bank["refilled_on"] == dt.date.today().isoformat()

    def test_profiles_have_separate_banks(self, mdb):
        mdb.adjust_arcade_bank(1, -500)
        assert mdb.get_arcade_bank(1)["balance"] == mdb.ARCADE_DAILY - 500
        assert mdb.get_arcade_bank(2)["balance"] == mdb.ARCADE_DAILY

    def test_spending_and_winning_move_the_balance(self, mdb):
        assert mdb.adjust_arcade_bank(1, -25)["balance"] == mdb.ARCADE_DAILY - 25
        assert mdb.adjust_arcade_bank(1, 100)["balance"] == mdb.ARCADE_DAILY + 75

    def test_you_cannot_stake_what_you_do_not_have(self, mdb):
        mdb.adjust_arcade_bank(1, -(mdb.ARCADE_DAILY - 10))
        out = mdb.adjust_arcade_bank(1, -50)
        assert out.get("refused") is True
        assert mdb.get_arcade_bank(1)["balance"] == 10, "and the balance is untouched"

    def test_spending_exactly_the_balance_is_allowed(self, mdb):
        out = mdb.adjust_arcade_bank(1, -mdb.ARCADE_DAILY)
        assert out["balance"] == 0
        assert not out.get("refused")

    def test_lifetime_totals_accumulate_separately(self, mdb):
        mdb.adjust_arcade_bank(1, -100)
        mdb.adjust_arcade_bank(1, 40)
        mdb.adjust_arcade_bank(1, -60)
        bank = mdb.get_arcade_bank(1)
        assert bank["lifetime_lost"] == 160
        assert bank["lifetime_won"] == 40

    def test_a_new_day_tops_back_up_to_the_allowance(self, mdb):
        mdb.adjust_arcade_bank(1, -(mdb.ARCADE_DAILY - 5))
        assert mdb.get_arcade_bank(1)["balance"] == 5
        # Reading on a later day is what triggers the refill; there is no
        # scheduler and none is wanted.
        self._roll_day(mdb)
        assert mdb.get_arcade_bank(1)["balance"] == mdb.ARCADE_DAILY

    @staticmethod
    def _roll_day(mdb):
        """Pretend midnight has passed. Reading the bank is what refills it."""
        with mdb._get_connection() as conn:
            conn.execute("UPDATE arcade_bank SET refilled_on = '2020-01-01' "
                         "WHERE profile_id = 1")
            conn.commit()

    def test_winnings_above_the_allowance_survive_the_refill(self, mdb):
        # The refill is a FLOOR, not a reset — you keep what you won.
        mdb.adjust_arcade_bank(1, 5000)                     # 15,000
        self._roll_day(mdb)
        assert mdb.get_arcade_bank(1)["balance"] == mdb.ARCADE_DAILY + 5000

    def test_the_refill_never_adds_the_allowance(self, mdb):
        # Sitting out a month must not make you rich for doing nothing: the
        # floor tops you UP to the allowance, it does not hand you another one.
        for _ in range(3):
            self._roll_day(mdb)
            assert mdb.get_arcade_bank(1)["balance"] == mdb.ARCADE_DAILY

    def test_junk_deltas_leave_the_balance_alone(self, mdb):
        for bad in (None, "lots", 1.5e400, [], {}):
            out = mdb.adjust_arcade_bank(1, bad)
            assert out["balance"] == mdb.ARCADE_DAILY, repr(bad)


class TestWiring:
    _API = (_ROOT / "api" / "chat.py").read_text(encoding="utf-8")
    _CHAT_JS = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8")

    def test_the_bank_is_per_profile(self):
        assert 'db.get_arcade_bank(int(getattr(g, "profile_id", 1) or 1))' in self._API

    def test_adjustments_are_bounded(self):
        # A typo or a bug must not be able to mint a fortune in one request.
        assert "_ARCADE_MAX_STAKE" in self._API
        assert "Amount out of range" in self._API

    def test_the_stake_is_debited_before_the_win_is_paid(self):
        # Two calls rather than one net adjustment: a spin interrupted halfway
        # should cost the stake, not silently pay out.
        assert "Debit first, then settle the win" in self._CHAT_JS

    def test_slots_are_not_on_the_protocol_bus(self):
        # A solo pull is nobody else's business and would only be room noise.
        assert "The slot machine is solo" in self._CHAT_JS

    def test_the_ui_says_the_money_is_not_worth_anything(self):
        assert "cannot be staked against another player" in self._CHAT_JS

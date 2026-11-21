"""device invite codes (PAIRING_SPEC.md 2, 8): entropy and alphabet,
expiry, single use, the 5-try lock, the live cap and the HMAC-only store."""

from __future__ import annotations

import hashlib
import hmac
import math
import sqlite3
from collections import Counter

import pytest

from core.connect import invite_codes as C

SECRET = bytes(range(32)).hex()


class _DB:
    def __init__(self, path):
        self.path = str(path)
        with self._get_connection() as c:
            C.ensure_tables(c.cursor())
            c.commit()

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def dump(self) -> str:
        with self._get_connection() as c:
            return "\n".join(str(tuple(r)) for r in c.execute("SELECT * FROM device_invite_codes"))


@pytest.fixture
def db(tmp_path):
    return _DB(tmp_path / "c.db")


# ── the code itself ──────────────────────────────────────────────────────

def test_the_alphabet_is_crockford_32_with_no_lookalikes():
    assert len(C.ALPHABET) == 32 == len(set(C.ALPHABET))
    assert not set("ILOU") & set(C.ALPHABET)


def test_a_code_is_16_symbols_about_80_bits():
    code = C.new_code()
    assert len(code) == 16 and set(code) <= set(C.ALPHABET)
    assert C.LENGTH * math.log2(len(C.ALPHABET)) == 80


def test_codes_spread_over_the_whole_alphabet():
    seen = Counter("".join(C.new_code() for _ in range(2000)))
    assert set(seen) == set(C.ALPHABET)
    # 32000 symbols, 1000 each expected: nothing wildly off
    assert min(seen.values()) > 800 and max(seen.values()) < 1200


def test_display_and_what_people_type():
    code = "0123456789ABCDEF"
    assert C.display(code) == "0123-4567-89AB-CDEF"
    assert C.normalize("0123-4567-89ab-cdef") == code
    assert C.normalize(" 0123 4567 89AB CDEF ") == code
    # I and L read as 1, O as 0
    assert C.normalize("O123-4567-89AB-CDEF") == code
    assert C.normalize("0I23-4567-89AB-CDEF") == "0123456789ABCDEF"
    assert C.normalize("0L23456789ABCDEF") == code
    for bad in ("U123456789ABCDEF", "0123", "0123456789ABCDEF0", "", None, 12, "0123-4567-89AB-CDE!"):
        assert C.normalize(bad) is None, bad


def test_the_code_hash_for_connect_is_sha256_of_the_canonical_code():
    # pinned: the connect service computes the same from the same 16 characters
    assert C.code_hash("0123456789ABCDEF") == (
        "2125b2c332b1113aae9bfc5e9f7e3b4c91d828cb942c2df1eeb02502eccae9e9")
    assert C.code_hash(C.normalize("0123-4567-89ab-cdef")) == C.code_hash("0123456789ABCDEF")


def test_the_store_holds_only_hmacs_and_the_hash(db):
    code, row = C.create(db, SECRET, now=1000.0)
    dump = db.dump()
    assert code not in dump and C.display(code) not in dump
    assert hmac.new(bytes.fromhex(SECRET), code.encode(), hashlib.sha256).hexdigest() in dump
    assert C.code_hash(code) in dump
    assert row["code_hash"] == C.code_hash(code)


# ── redemption ───────────────────────────────────────────────────────────

def test_a_code_works_once(db):
    code, row = C.create(db, SECRET, now=1000.0)
    assert C.redeem(db, SECRET, C.display(code).lower(), now=1001.0) == (C.REDEEMED, row["id"])
    assert C.redeem(db, SECRET, code, now=1002.0) == (C.INVALID, None)


def test_a_code_dies_after_10_minutes(db):
    code, _ = C.create(db, SECRET, now=1000.0)
    assert C.redeem(db, SECRET, code, now=1000.0 + 600) == (C.INVALID, None)
    code2, row2 = C.create(db, SECRET, now=1000.0)
    assert C.redeem(db, SECRET, code2, now=1000.0 + 599) == (C.REDEEMED, row2["id"])


def test_another_servers_secret_never_matches(db):
    code, _ = C.create(db, SECRET, now=1000.0)
    assert C.redeem(db, "ab" * 32, code, now=1001.0) == (C.INVALID, None)


def test_five_wrong_tries_lock_the_code_for_good(db):
    code, row = C.create(db, SECRET, now=1000.0)
    code_b, _ = C.create(db, SECRET, now=1000.0)
    wrong = code[:4] + ("0" * 12 if code[4:] != "0" * 12 else "1" * 12)
    for _ in range(5):
        assert C.redeem(db, SECRET, wrong, now=1001.0) == (C.INVALID, None)
    # locked: the right code no longer works, and it left the live list
    assert C.redeem(db, SECRET, code, now=1002.0) == (C.INVALID, None)
    assert [c["id"] for c in C.live_codes(db, now=1002.0)] != [] and all(
        c["id"] != row["id"] for c in C.live_codes(db, now=1002.0))
    # another code is untouched
    assert C.redeem(db, SECRET, code_b, now=1003.0)[0] == C.REDEEMED


def test_four_wrong_tries_and_then_the_right_one(db):
    code, row = C.create(db, SECRET, now=1000.0)
    wrong = code[:4] + ("0" * 12 if code[4:] != "0" * 12 else "1" * 12)
    for _ in range(4):
        C.redeem(db, SECRET, wrong, now=1001.0)
    assert C.redeem(db, SECRET, code, now=1002.0) == (C.REDEEMED, row["id"])


def test_a_wrong_guess_that_names_no_code_counts_against_nothing(db):
    code, _ = C.create(db, SECRET, now=1000.0)
    other = ("Z" if code[0] != "Z" else "Y") + code[1:]
    for _ in range(10):
        C.redeem(db, SECRET, other, now=1001.0)
    assert C.redeem(db, SECRET, code, now=1002.0)[0] == C.REDEEMED


def test_at_most_20_live(db):
    for _ in range(20):
        C.create(db, SECRET, now=1000.0)
    with pytest.raises(C.TooManyLive):
        C.create(db, SECRET, now=1000.0)
    # an expired one frees its place
    assert C.create(db, SECRET, now=1000.0 + 601)[0]


def test_cancel(db):
    code, row = C.create(db, SECRET, now=1000.0)
    assert C.cancel(db, row["id"])
    assert not C.cancel(db, row["id"])
    assert C.redeem(db, SECRET, code, now=1001.0) == (C.INVALID, None)
    assert C.live_codes(db, now=1001.0) == []


def test_the_live_list_says_how_long_is_left_and_never_the_code(db):
    code, _ = C.create(db, SECRET, note="Car", now=1000.0)
    live = C.live_codes(db, now=1100.0)
    assert len(live) == 1 and live[0]["seconds_left"] == 500 and live[0]["note"] == "Car"
    assert code not in str(live)
    assert set(live[0]) == {"id", "note", "created_by", "created_at", "expires_at", "wrong_tries",
                            "seconds_left"}

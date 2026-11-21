"""device invite codes (PAIRING_SPEC.md 2).

only the admin makes them. a code is 16 characters from Crockford's base32
alphabet (no I, L, O or U), about 80 bits, shown as ``XXXX-XXXX-XXXX-XXXX``.
it lives 10 minutes, is used once, and at most 20 are live at a time.

the code itself is never stored:

* ``code_hmac`` = HMAC-SHA256(server root secret, code): what redemption
  checks against;
* ``selector_hmac`` = HMAC-SHA256(server root secret, "selector:" + the
  first 4 characters): which code a redemption means, so a wrong guess
  can be counted against that code and 5 of them burn it. the 4 characters
  only pick the row; the whole code still has to match;
* ``code_hash`` = SHA-256 of the code (UTF-8) as lowercase hex: what the
  server will register with the connect service so an app can look it up
  (FOR_MUSE.md, Nov 20 answers). nothing reads it yet.

"the code" in all three is its canonical form: the 16 characters,
uppercase, no dashes. typing it with dashes, spaces, lowercase, or I/L for
1 and O for 0 reads as the same code.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
LENGTH = 16
SELECTOR_LENGTH = 4
LIFETIME = 10 * 60
MAX_LIVE = 20
MAX_WRONG_TRIES = 5
# Crockford's decoding: these read as the digits they look like
_READS_AS = {"I": "1", "L": "1", "O": "0"}


def ensure_tables(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS device_invite_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code_hmac TEXT NOT NULL UNIQUE,
            selector_hmac TEXT NOT NULL,
            code_hash TEXT NOT NULL,
            note TEXT,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at REAL NOT NULL,
            wrong_tries INTEGER NOT NULL DEFAULT 0,
            used_at TIMESTAMP,
            used_by_device INTEGER,
            cancelled_at TIMESTAMP,
            locked_at TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_device_invite_codes_selector "
                   "ON device_invite_codes (selector_hmac)")


# ── the code ─────────────────────────────────────────────────────────────

def new_code() -> str:
    """16 random symbols, canonical form. 16 x 5 bits = 80 bits."""
    return "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def display(code: str) -> str:
    return "-".join(code[i:i + 4] for i in range(0, len(code), 4))


def normalize(raw: Any) -> Optional[str]:
    """the canonical form of what someone typed or scanned, or None when it
    can't be a code."""
    if not isinstance(raw, str):
        return None
    out = []
    for ch in raw.upper():
        if ch in " -\t":
            continue
        ch = _READS_AS.get(ch, ch)
        if ch not in ALPHABET:
            return None
        out.append(ch)
    code = "".join(out)
    return code if len(code) == LENGTH else None


def code_hash(code: str) -> str:
    """SHA-256(code UTF-8) hex, for the connect service."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _hmac(secret_hex: str, message: str) -> str:
    return hmac.new(bytes.fromhex(secret_hex), message.encode("utf-8"), hashlib.sha256).hexdigest()


def code_hmac(secret_hex: str, code: str) -> str:
    return _hmac(secret_hex, code)


def selector_hmac(secret_hex: str, code: str) -> str:
    return _hmac(secret_hex, "selector:" + code[:SELECTOR_LENGTH])


# ── the store ────────────────────────────────────────────────────────────

_LIVE_SQL = ("used_at IS NULL AND cancelled_at IS NULL AND locked_at IS NULL AND expires_at > ?")


class TooManyLive(Exception):
    pass


def live_codes(db, *, now: Optional[float] = None) -> List[Dict[str, Any]]:
    now = time.time() if now is None else now
    with db._get_connection() as conn:
        rows = conn.execute(
            "SELECT id, note, created_by, created_at, expires_at, wrong_tries FROM device_invite_codes "
            f"WHERE {_LIVE_SQL} ORDER BY id DESC", (now,)).fetchall()  # noqa: S608 - a constant
    out = []
    for r in rows:
        d = dict(r)
        d["seconds_left"] = max(0, int(float(d["expires_at"]) - now))
        out.append(d)
    return out


def create(db, secret_hex: str, *, created_by: Optional[int] = None, note: str = "",
           now: Optional[float] = None) -> Tuple[str, Dict[str, Any]]:
    """(code, row) for a new code. raises TooManyLive past MAX_LIVE."""
    now = time.time() if now is None else now
    with db._get_connection() as conn:
        live = conn.execute(f"SELECT selector_hmac FROM device_invite_codes WHERE {_LIVE_SQL}",  # noqa: S608
                            (now,)).fetchall()
        if len(live) >= MAX_LIVE:
            raise TooManyLive()
        taken = {r[0] for r in live}
        for _ in range(20):
            code = new_code()
            sel = selector_hmac(secret_hex, code)
            if sel not in taken:
                break
        else:  # pragma: no cover - 20 collisions in a 2^20 space
            raise RuntimeError("could not make a code")
        cur = conn.execute(
            "INSERT INTO device_invite_codes (code_hmac, selector_hmac, code_hash, note, created_by, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (code_hmac(secret_hex, code), sel, code_hash(code), str(note or "").strip()[:80] or None,
             created_by, now + LIFETIME))
        conn.commit()
        row_id = int(cur.lastrowid)
    return code, {"id": row_id, "expires_at": now + LIFETIME, "seconds_left": LIFETIME,
                  "code_hash": code_hash(code)}


def cancel(db, code_id: int) -> bool:
    with db._get_connection() as conn:
        cur = conn.execute("UPDATE device_invite_codes SET cancelled_at = CURRENT_TIMESTAMP "
                           "WHERE id = ? AND used_at IS NULL AND cancelled_at IS NULL", (int(code_id),))
        conn.commit()
        return cur.rowcount > 0


# what redeem() answers
REDEEMED = "redeemed"
INVALID = "invalid"     # no such live code: unknown, expired, used, cancelled or locked


def redeem(db, secret_hex: str, raw: Any, *, now: Optional[float] = None) -> Tuple[str, Optional[int]]:
    """(REDEEMED, code id) and the code is burnt, or (INVALID, None). a
    code whose first 4 characters match but the rest don't gets a wrong try;
    the 5th locks it for good. one UPDATE burns it, so two redemptions of
    one code can't both win."""
    now = time.time() if now is None else now
    code = normalize(raw)
    if code is None:
        return INVALID, None
    want = code_hmac(secret_hex, code)
    sel = selector_hmac(secret_hex, code)
    with db._get_connection() as conn:
        rows = conn.execute(
            f"SELECT id, code_hmac FROM device_invite_codes WHERE selector_hmac = ? AND {_LIVE_SQL}",  # noqa: S608
            (sel, now)).fetchall()
        for row in rows:
            if hmac.compare_digest(str(row["code_hmac"]), want):
                cur = conn.execute(
                    "UPDATE device_invite_codes SET used_at = CURRENT_TIMESTAMP "
                    f"WHERE id = ? AND {_LIVE_SQL}", (row["id"], now))  # noqa: S608
                conn.commit()
                return (REDEEMED, int(row["id"])) if cur.rowcount else (INVALID, None)
        # the first 4 characters named a live code but the rest is wrong
        for row in rows:
            conn.execute(
                "UPDATE device_invite_codes SET wrong_tries = wrong_tries + 1, "
                "locked_at = CASE WHEN wrong_tries + 1 >= ? THEN CURRENT_TIMESTAMP ELSE locked_at END "
                "WHERE id = ?", (MAX_WRONG_TRIES, row["id"]))
        conn.commit()
    return INVALID, None


def mark_device(db, code_id: int, device_id: int) -> None:
    with db._get_connection() as conn:
        conn.execute("UPDATE device_invite_codes SET used_by_device = ? WHERE id = ?",
                     (int(device_id), int(code_id)))
        conn.commit()


def sweep(db, *, now: Optional[float] = None, keep_days: int = 7) -> int:
    """codes past their life by more than ``keep_days``: gone."""
    now = time.time() if now is None else now
    with db._get_connection() as conn:
        cur = conn.execute("DELETE FROM device_invite_codes WHERE expires_at < ?",
                           (now - keep_days * 86400,))
        conn.commit()
        return cur.rowcount or 0


__all__ = ["ALPHABET", "LENGTH", "LIFETIME", "MAX_LIVE", "MAX_WRONG_TRIES", "ensure_tables", "new_code",
           "display", "normalize", "code_hash", "code_hmac", "selector_hmac", "TooManyLive", "live_codes",
           "create", "cancel", "REDEEMED", "INVALID", "redeem", "mark_device", "sweep"]

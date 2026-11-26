"""import plex users (#2319).

an admin makes profiles ahead of time for the people their plex server is
shared with, so nobody has to sign in to soulsync first (a seerr client can
ask for them right away). each one is linked to their plex account id, the
same record sign in with plex writes, so their first plex sign-in lands on
that profile even while sign-in is not allowed to make new profiles.

- who: the admin's plex friends and home users that can see THIS server
  (plex.tv's user list, with the admin token soulsync is set up with)
- already here: a profile linked to that plex account (by id). a profile
  with the same name, or the plex home user link, is only a guess: the admin
  picks whether to link it or make a new one
- never twice: a plex account already linked stays where it is, an admin
  profile is never linked (admins come in through the owner check)
- new profiles follow the template the admin picks, the sign-up template
  unless they say otherwise
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from utils.logging_config import get_logger

logger = get_logger("security.plex_import")

AVATAR_MAX_BYTES = 3 * 1024 * 1024


@dataclass
class PlexUser:
    id: str
    username: str
    title: str
    email: str = ""
    thumb: str = ""
    home: bool = False
    # a managed home user has no plex.tv login of their own, so plex's pin
    # page can't sign them in: they get in with a password or the picker
    managed: bool = False
    # the plex object, for the server token at import time
    raw: Any = field(default=None, repr=False, compare=False)

    @property
    def name(self) -> str:
        return self.username or self.title or "Plex user"


def _shares_server(user, machine_id: Optional[str]) -> bool:
    """can this plex user see THIS server? a friend has a server share for
    it. home users often come back with no shares listed (their access
    comes with the home), so no shares at all counts for them"""
    if not machine_id:
        return True
    try:
        shares = list(getattr(user, "servers", None) or [])
    except Exception:  # noqa: BLE001 - plexapi parses lazily
        shares = []
    if any(str(getattr(s, "machineIdentifier", "")) == str(machine_id) for s in shares):
        return True
    return bool(getattr(user, "home", False)) and not shares


def server_users(account, machine_id: Optional[str]) -> List[PlexUser]:
    """everyone on the admin's plex account who can see this server.
    `account` is a plexapi MyPlexAccount (or anything with users())"""
    out: List[PlexUser] = []
    seen = set()
    for u in account.users():
        uid = getattr(u, "id", None)
        if uid in (None, "") or str(uid) in seen:
            continue
        if not _shares_server(u, machine_id):
            continue
        seen.add(str(uid))
        home = bool(getattr(u, "home", False))
        restricted = getattr(u, "restricted", False)
        managed = home and (restricted in (True, 1, "1", "true") or not (getattr(u, "username", "") or ""))
        out.append(PlexUser(
            id=str(uid),
            username=str(getattr(u, "username", "") or ""),
            title=str(getattr(u, "title", "") or ""),
            email=str(getattr(u, "email", "") or ""),
            thumb=str(getattr(u, "thumb", "") or ""),
            home=home,
            managed=bool(managed),
            raw=u,
        ))
    out.sort(key=lambda p: p.name.casefold())
    return out


# ── who's already here ───────────────────────────────────────────────────

def _profile_rows(db) -> List[Dict[str, Any]]:
    """id, name, is_admin, plex_account_id, plex_home_user_id per profile"""
    with db._get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()}
        pick = ["id", "name", "is_admin"] + [c for c in ("plex_account_id", "plex_home_user_id") if c in cols]
        rows = conn.execute("SELECT %s FROM profiles ORDER BY id" % ", ".join(pick)).fetchall()
    out = []
    for r in rows:
        d = {k: r[i] for i, k in enumerate(pick)}
        d["is_admin"] = bool(d.get("is_admin")) or int(d["id"]) == 1
        out.append(d)
    return out


def _norm(s: str) -> str:
    return " ".join(str(s or "").split()).casefold()


def match(users: Iterable[PlexUser], profiles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """each plex user with the profile that is (or may be) them:
    {"profile": {id, name, is_admin}, "how": "linked" | "home" | "name"} or
    no profile. linked is certain (plex sign-in's own record); home and name
    are guesses the admin confirms. a guess never points at an admin or at
    a profile linked to another plex account"""
    from core.security.plex_signin import profile_name_for

    by_account = {str(p["plex_account_id"]): p for p in profiles if p.get("plex_account_id")}
    by_home = {str(p["plex_home_user_id"]): p for p in profiles if p.get("plex_home_user_id")}
    by_name: Dict[str, Dict[str, Any]] = {}
    for p in profiles:
        by_name.setdefault(_norm(p.get("name")), p)
    out = []
    for u in users:
        hit, how = by_account.get(u.id), "linked"
        if hit is None:
            hit, how = by_home.get(u.id), "home"
            if hit is None:
                for key in (u.username, u.title, profile_name_for(u.name),
                            u.email.split("@")[0] if u.email else ""):
                    if key and _norm(key) in by_name:
                        hit, how = by_name[_norm(key)], "name"
                        break
            if hit is not None and (hit["is_admin"] or hit.get("plex_account_id")
                                    or (hit.get("plex_home_user_id") and str(hit["plex_home_user_id"]) != u.id)):
                hit = None
        out.append({"user": u, "profile": ({"id": int(hit["id"]), "name": hit["name"],
                                            "is_admin": hit["is_admin"]} if hit else None),
                    "how": how if hit else None})
    return out


def listing(db, users: List[PlexUser]) -> List[Dict[str, Any]]:
    """the rows the import dialog shows"""
    rows = []
    for m in match(users, _profile_rows(db)):
        u = m["user"]
        rows.append({"id": u.id, "username": u.username, "title": u.title, "name": u.name,
                     "email": u.email, "thumb": u.thumb if _safe_avatar_url(u.thumb) else "",
                     "home": u.home, "managed": u.managed,
                     "profile": m["profile"], "match": m["how"]})
    return rows


# ── importing ────────────────────────────────────────────────────────────

def _safe_avatar_url(url: str) -> bool:
    """only plex's own https avatar hosts are fetched: the url comes from
    plex.tv, but soulsync fetching any url it's handed is an ssrf"""
    try:
        u = urlparse(url or "")
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (host == "plex.tv" or host.endswith(".plex.tv"))


def fetch_avatar(url: str) -> Optional[bytes]:
    if not _safe_avatar_url(url):
        return None
    import requests
    try:
        with requests.get(url, timeout=10, stream=True, allow_redirects=False) as r:
            if r.status_code != 200:
                return None
            data = r.raw.read(AVATAR_MAX_BYTES + 1, decode_content=True)
        return data if data and len(data) <= AVATAR_MAX_BYTES else None
    except Exception as e:  # noqa: BLE001 - no avatar is fine
        logger.debug("plex import: avatar fetch failed: %s", e)
        return None


@dataclass
class ImportResult:
    plex_id: str
    name: str
    status: str  # created | linked | exists | skipped | failed
    profile_id: Optional[int] = None
    message: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"plex_id": self.plex_id, "name": self.name, "status": self.status,
                "profile_id": self.profile_id, "message": self.message}


def _server_token(user: PlexUser, machine_id: Optional[str]) -> Optional[str]:
    """the user's own token for this server, as sign-in would store it, so
    their playlists land in their plex account before they ever sign in.
    best effort: sign-in fills it in otherwise"""
    if not machine_id or user.raw is None or not hasattr(user.raw, "get_token"):
        return None
    try:
        return user.raw.get_token(machine_id) or None
    except Exception as e:  # noqa: BLE001
        logger.debug("plex import: no server token for %s: %s", user.id, e)
        return None


def _home_link(db, profile_id: int) -> Optional[str]:
    with db._get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()}
        if "plex_home_user_id" not in cols:
            return None
        row = conn.execute("SELECT plex_home_user_id FROM profiles WHERE id = ?", (int(profile_id),)).fetchone()
    return str(row[0]) if row and row[0] else None


def _act_as(db, profile_id: int, user: PlexUser, machine_id: Optional[str]) -> None:
    """store their server token the way sign-in does. a profile already
    acting as another plex user keeps that: the admin set it on purpose"""
    current = _home_link(db, profile_id)
    if current and current != user.id:
        return
    token = _server_token(user, machine_id)
    if token:
        db.set_profile_plex_home_user(profile_id, user.id, user.name, token)


def import_users(db, users: List[PlexUser], picks: List[Dict[str, Any]], *,
                 template_id: Optional[int], machine_id: Optional[str] = None,
                 save_avatar: Optional[Callable[[int, bytes], Optional[str]]] = None,
                 avatar_fetcher: Callable[[str], Optional[bytes]] = fetch_avatar) -> List[ImportResult]:
    """import the picked users. a pick is {"id": plex id} for a new profile,
    or {"id", "profile_id"} to link one that's already here. users is the
    fresh list from plex: nothing the browser sends is taken as a name or
    an avatar. the profiles and links are made under sign-in's lock, so a
    plex sign-in finishing at the same moment can't make a second profile
    for the same account; the slow plex.tv calls (server tokens, pictures)
    come after it"""
    from core.permission_store import place_on_template
    from core.security.plex_signin import _create_profile, _sign_in_lock
    from core.security.signup_defaults import SignupAccess

    by_id = {u.id: u for u in users}
    results: List[ImportResult] = []
    done = set()
    after: List[tuple] = []  # (profile id, user, made here)
    with _sign_in_lock:
        for pick in picks:
            pid = str((pick or {}).get("id") or "").strip()
            if not pid or pid in done:
                continue
            done.add(pid)
            user = by_id.get(pid)
            if user is None:
                results.append(ImportResult(pid, "", "skipped",
                                            message="Not on your Plex server's user list any more"))
                continue
            holder = db.get_profile_by_plex_account(user.id)
            if holder:
                results.append(ImportResult(pid, user.name, "exists", int(holder["id"]),
                                            "Already %s in SoulSync" % holder.get("name")))
                continue
            target = pick.get("profile_id")
            if target not in (None, ""):
                try:
                    target = int(target)
                except (TypeError, ValueError):
                    results.append(ImportResult(pid, user.name, "failed", message="Not a profile"))
                    continue
                prof = db.get_profile(target)
                if not prof:
                    results.append(ImportResult(pid, user.name, "failed", message="That profile is gone"))
                    continue
                if prof.get("is_admin") or target == 1:
                    results.append(ImportResult(pid, user.name, "failed", target,
                                                "An admin profile can't be linked to a Plex user"))
                    continue
                if _linked_elsewhere(db, target):
                    results.append(ImportResult(pid, user.name, "failed", target,
                                                "%s is linked to another Plex account" % prof.get("name")))
                    continue
                if not db.set_profile_plex_account(target, user.id):
                    results.append(ImportResult(pid, user.name, "failed", target, "Couldn't save the link"))
                    continue
                after.append((target, user, False))
                results.append(ImportResult(pid, user.name, "linked", target,
                                            "Linked to %s" % prof.get("name")))
                continue
            new_id = _create_profile(db, user.name, can_download=False, access=SignupAccess())
            if new_id is None:
                results.append(ImportResult(pid, user.name, "failed", message="Couldn't make the profile"))
                continue
            if not db.set_profile_plex_account(new_id, user.id):
                # a profile no plex sign-in reaches is not an import: take it back
                db.delete_profile(new_id)
                results.append(ImportResult(pid, user.name, "failed", message="Couldn't link the profile"))
                continue
            if template_id is not None and not place_on_template(db, new_id, template_id):
                logger.warning("plex import: profile %s kept the sign-up access, template %s didn't take",
                               new_id, template_id)
            after.append((new_id, user, True))
            name = (db.get_profile(new_id) or {}).get("name") or user.name
            results.append(ImportResult(pid, user.name, "created", new_id, "Added as %s" % name))
    for profile_id, user, made in after:
        _act_as(db, profile_id, user, machine_id)
        if made and save_avatar and user.thumb:
            raw = avatar_fetcher(user.thumb)
            if raw:
                try:
                    save_avatar(profile_id, raw)
                except Exception as e:  # noqa: BLE001 - the profile stands without one
                    logger.debug("plex import: avatar not saved for %s: %s", profile_id, e)
    if any(r.status in ("created", "linked") for r in results):
        try:
            # plays already recorded for them move to their own pile now
            from core.listening_scope import reattribute_media_plays
            reattribute_media_plays(db, "plex")
        except Exception as e:  # noqa: BLE001
            logger.debug("plex import: re-filing plays failed, the next poll will: %s", e)
    return results


def _linked_elsewhere(db, profile_id: int) -> bool:
    with db._get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()}
        if "plex_account_id" not in cols:
            return False
        row = conn.execute("SELECT plex_account_id FROM profiles WHERE id = ?", (int(profile_id),)).fetchone()
    return bool(row and row[0])

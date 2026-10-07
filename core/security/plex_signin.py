"""sign in with plex.

plex's own pin flow, the one overseerr and tautulli use: soulsync asks
plex.tv for a pin, the user approves it on plex's site (soulsync never sees
their password), soulsync reads back their account token. from that:

- the account must be able to see THIS server, else no entry
- the server's owner (plex says so: the server is `owned` in their
  resources) signs in as the admin profile
- an account that signed in before signs in as the profile it got then.
  only plex sign-in records that; the home-user link never counts, since
  any profile can point it at any unprotected home user
- anyone else gets a new profile, if the admin allows it, with the admin's
  default role (request-only unless they say otherwise)

the profile then acts as that user on the server: their server token is
stored the way the home-user link stores one, so playlists land in their own
plex account through the existing per-user view. the account token itself is
never kept.
"""

from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import requests

from utils.logging_config import get_logger

logger = get_logger("security.plex_signin")

PLEX_PINS_URL = "https://plex.tv/api/v2/pins"
PLEX_AUTH_URL = "https://app.plex.tv/auth#?"
PRODUCT = "SoulSync"
TIMEOUT = 10


# what a profile made by plex sign-in can open. sign-in is an open invite to
# everyone the admin shares plex with, so it starts on finding and asking
# for music, never the machinery (sync, automations, import, downloads,
# tools). the admin widens it per person in profile management
SIGNUP_PAGES = ['discover', 'search', 'library', 'artist-detail', 'wishlist', 'stats', 'help', 'issues']
SIGNUP_HOME_PAGE = 'discover'

_cid_lock = threading.Lock()
# find-or-create a profile is one step: two tabs (or a double click) finishing
# at once must not make two profiles for one plex account
_sign_in_lock = threading.Lock()


def client_identifier(config_manager) -> str:
    """this install's stable plex client id. plex ties a pin to the client
    that made it, so it must not change between start and check (two first
    sign-ins racing would each make one, and the loser's pin would 404)"""
    with _cid_lock:
        cid = config_manager.get("plex_signin.client_id", "") or ""
        if not cid:
            cid = f"soulsync-{uuid.uuid4()}"
            config_manager.set("plex_signin.client_id", cid)
        return cid


def _headers(cid: str, token: Optional[str] = None) -> dict:
    h = {
        "Accept": "application/json",
        "X-Plex-Product": PRODUCT,
        "X-Plex-Client-Identifier": cid,
    }
    if token:
        h["X-Plex-Token"] = token
    return h


def start_pin(cid: str, forward_url: str = "") -> dict:
    """a fresh pin and the plex page that approves it. raises on failure"""
    resp = requests.post(PLEX_PINS_URL, params={"strong": "true"}, headers=_headers(cid), timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    params = {"clientID": cid, "code": data["code"], "context[device][product]": PRODUCT}
    if forward_url:
        params["forwardUrl"] = forward_url
    return {"id": int(data["id"]), "url": PLEX_AUTH_URL + urlencode(params)}


def check_pin(cid: str, pin_id: int) -> Optional[str]:
    """the account token once the user approved the pin, else None"""
    resp = requests.get(f"{PLEX_PINS_URL}/{int(pin_id)}", headers=_headers(cid), timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("authToken") or None


@dataclass
class PlexAccount:
    id: str
    username: str
    server_token: Optional[str]
    # plex's own word that this account owns the server, not a guess from
    # whose token soulsync happens to be configured with
    owns_server: bool = False


def resolve_account(account_token: str, machine_id: str) -> PlexAccount:
    """who signed in, and their access token for this server (None when
    the account can't see it). raises when plex.tv won't answer"""
    from plexapi.myplex import MyPlexAccount

    account = MyPlexAccount(token=account_token)
    server_token = None
    owns = False
    for resource in account.resources():
        provides = getattr(resource, "provides", "") or ""
        if "server" in provides and resource.clientIdentifier == machine_id and resource.accessToken:
            server_token = resource.accessToken
            owns = bool(getattr(resource, "owned", False))
            break
    name = getattr(account, "username", None) or getattr(account, "title", None) or "Plex user"
    return PlexAccount(id=str(account.id), username=str(name), server_token=server_token, owns_server=owns)


@dataclass
class SignInResult:
    profile_id: Optional[int] = None
    error: Optional[str] = None
    created: bool = False


def sign_in(
    db,
    account: PlexAccount,
    *,
    allow_create: bool,
    default_can_download: bool,
) -> SignInResult:
    """map a signed-in plex account to a soulsync profile (see module doc)"""
    with _sign_in_lock:
        return _sign_in(db, account, allow_create=allow_create, default_can_download=default_can_download)


def _admin_profile_id(db) -> Optional[int]:
    """the admin profile the owner signs in as: profile 1, the one setup
    makes and nothing can delete, as long as it really is an admin"""
    p = db.get_profile(1) or {}
    return 1 if p.get("is_admin") else None


def _sign_in(db, account: PlexAccount, *, allow_create: bool, default_can_download: bool) -> SignInResult:
    if not account.server_token:
        return SignInResult(error="That Plex account doesn't have access to this server")

    if account.owns_server:
        # the server's owner is the admin. the admin acts as the app account,
        # so no per-user link is stored for it
        admin = _admin_profile_id(db)
        if admin is None:
            return SignInResult(error="SoulSync has no admin profile to sign you in to")
        return SignInResult(profile_id=admin)

    # identity comes only from plex sign-in's own record. the home-user link
    # can't be trusted for it: any profile can point that at any unprotected
    # home user, so trusting it would sign that user in as someone else
    profile = db.get_profile_by_plex_account(account.id)
    if profile:
        if profile.get("disabled"):
            return SignInResult(error="This profile is turned off. Ask your admin.")
        if profile.get("is_admin"):
            # admin is reached through the owner check above and nowhere else
            logger.warning("Plex sign-in: refused, account %s is recorded on an admin profile", account.id)
            return SignInResult(error="This Plex account can't sign in as an admin profile")
        # refresh the stored server token: it changes when access is re-shared
        db.set_profile_plex_home_user(profile["id"], account.id, account.username, account.server_token)
        return SignInResult(profile_id=profile["id"])

    if not allow_create:
        return SignInResult(error="Your Plex account isn't linked to a SoulSync profile yet. Ask your admin.")

    profile_id = _create_profile(db, account.username, can_download=default_can_download)
    if profile_id is None:
        return SignInResult(error="Couldn't create a profile for your Plex account")
    db.set_profile_plex_account(profile_id, account.id)
    db.set_profile_plex_home_user(profile_id, account.id, account.username, account.server_token)
    logger.info("Plex sign-in: created profile %s for Plex user '%s'", profile_id, account.username)
    return SignInResult(profile_id=profile_id, created=True)


def profile_name_for(username: str) -> str:
    """a profile name from a plex username. it comes from outside, so only
    plain characters survive: letters, digits, spaces and . _ -. no quotes,
    so a name can never close an html attribute wherever it's shown"""
    cleaned = re.sub(r"[^\w .\-]", "", username or "", flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()[:36]
    return cleaned or "Plex user"


def _create_profile(db, username: str, *, can_download: bool) -> Optional[int]:
    """a profile named after the plex user; a taken name gets a number"""
    base = profile_name_for(username)
    for n in range(0, 50):
        name = base if n == 0 else f"{base} {n + 1}"
        if db.get_profile_by_name(name):
            continue
        pid = db.create_profile(name, can_download=can_download,
                                allowed_pages=list(SIGNUP_PAGES), home_page=SIGNUP_HOME_PAGE)
        if pid:
            return pid
    return None


def server_machine_id(plex_client) -> Optional[str]:
    try:
        if plex_client is None or not plex_client.ensure_connection():
            return None
        return plex_client.server.machineIdentifier
    except Exception as e:  # noqa: BLE001
        logger.warning("Plex sign-in: could not read the server id: %s", e)
        return None

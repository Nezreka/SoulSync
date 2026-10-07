"""sign in with plex.

plex's own pin flow, the one overseerr and tautulli use: soulsync asks
plex.tv for a pin, the user approves it on plex's site (soulsync never sees
their password), soulsync reads back their account token. from that:

- the account must be able to see THIS server, else no entry
- the server owner signs in as the admin profile
- an account already linked to a profile (the same plex.tv user id the
  home-user link stores) signs in as that profile
- anyone else gets a new profile, if the admin allows it, with the admin's
  default role (request-only unless they say otherwise)

the profile then acts as that user on the server: their server token is
stored the way the home-user link stores one, so playlists land in their own
plex account through the existing per-user view. the account token itself is
never kept.
"""

from __future__ import annotations

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


def client_identifier(config_manager) -> str:
    """this install's stable plex client id. plex ties a pin to the client
    that made it, so it must not change between start and check"""
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


def resolve_account(account_token: str, machine_id: str) -> PlexAccount:
    """who signed in, and their access token for this server (None when
    the account can't see it). raises when plex.tv won't answer"""
    from plexapi.myplex import MyPlexAccount

    account = MyPlexAccount(token=account_token)
    server_token = None
    for resource in account.resources():
        provides = getattr(resource, "provides", "") or ""
        if "server" in provides and resource.clientIdentifier == machine_id and resource.accessToken:
            server_token = resource.accessToken
            break
    name = getattr(account, "username", None) or getattr(account, "title", None) or "Plex user"
    return PlexAccount(id=str(account.id), username=str(name), server_token=server_token)


@dataclass
class SignInResult:
    profile_id: Optional[int] = None
    error: Optional[str] = None
    created: bool = False


def sign_in(
    db,
    account: PlexAccount,
    *,
    owner_account_id: Optional[str],
    allow_create: bool,
    default_can_download: bool,
) -> SignInResult:
    """map a signed-in plex account to a soulsync profile (see module doc)"""
    if not account.server_token:
        return SignInResult(error="That Plex account doesn't have access to this server")

    if owner_account_id and account.id == str(owner_account_id):
        # the server's owner is the admin. the admin acts as the app account,
        # so no per-user link is stored for it
        return SignInResult(profile_id=1)

    profile = db.get_profile_by_plex_user(account.id)
    if profile:
        if profile.get("disabled"):
            return SignInResult(error="This profile is turned off. Ask your admin.")
        # refresh the stored server token: it changes when access is re-shared
        db.set_profile_plex_home_user(profile["id"], account.id, account.username, account.server_token)
        return SignInResult(profile_id=profile["id"])

    if not allow_create:
        return SignInResult(error="Your Plex account isn't linked to a SoulSync profile yet. Ask your admin.")

    profile_id = _create_profile(db, account.username, can_download=default_can_download)
    if profile_id is None:
        return SignInResult(error="Couldn't create a profile for your Plex account")
    db.set_profile_plex_home_user(profile_id, account.id, account.username, account.server_token)
    logger.info("Plex sign-in: created profile %s for Plex user '%s'", profile_id, account.username)
    return SignInResult(profile_id=profile_id, created=True)


def _create_profile(db, username: str, *, can_download: bool) -> Optional[int]:
    """a profile named after the plex user; a taken name gets a number"""
    base = (username or "Plex user").strip()[:36] or "Plex user"
    for n in range(0, 50):
        name = base if n == 0 else f"{base} {n + 1}"
        if db.get_profile_by_name(name):
            continue
        pid = db.create_profile(name, can_download=can_download)
        if pid:
            return pid
    return None


def owner_account_id(plex_client) -> Optional[str]:
    """the plex.tv id of the account the app's own token belongs to: the
    server owner, as far as soulsync is concerned. None when plex isn't set up"""
    try:
        if plex_client is None or not plex_client.ensure_connection():
            return None
        return str(plex_client._account().id)
    except Exception as e:  # noqa: BLE001 - no owner means nobody maps to admin
        logger.warning("Plex sign-in: could not read the server owner's account: %s", e)
        return None


def server_machine_id(plex_client) -> Optional[str]:
    try:
        if plex_client is None or not plex_client.ensure_connection():
            return None
        return plex_client.server.machineIdentifier
    except Exception as e:  # noqa: BLE001
        logger.warning("Plex sign-in: could not read the server id: %s", e)
        return None

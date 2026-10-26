"""sign in with oidc.

any openid connect provider (pocket id, authentik, authelia, keycloak, ...).
the admin gives soulsync the provider's issuer url, a client id and secret;
soulsync reads the rest from the provider's discovery document. the flow is
the authorization code flow with pkce: the browser goes to the provider, the
user signs in there (soulsync never sees their password), the provider sends
the browser back with a one-time code, and soulsync swaps that code for the
user's id token server to server.

the id token comes straight from the provider's token endpoint over tls, so
per openid connect core 3.1.3.7 that tls connection stands in for checking
the token's signature. everything else is still checked: issuer, audience,
expiry and the nonce this browser was given. that's why the issuer and the
token endpoint must be https (loopback excepted, for local testing).

from the account:

- a required group set and the account not in it: no entry
- an account already linked to a profile signs in as that profile
- anyone else gets a new profile, if the admin allows it, with the same
  starting pages as plex sign-in (request-only unless they say otherwise)

identity is the issuer plus the account's ``sub``, never an email address:
a user can often change their email at the provider, and matching on it
would let them take over whoever had it first. claims never make anyone an
admin. the admin's own profile gets oidc only by connecting it from my
account, while already signed in.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urlparse

import requests

from core.security.signup_defaults import SignupAccess, create_signup_profile
from utils.logging_config import get_logger

logger = get_logger("security.oidc_signin")

TIMEOUT = 10
DEFAULT_SCOPES = "openid profile email"
DEFAULT_GROUPS_CLAIM = "groups"
DEFAULT_BUTTON_LABEL = "Sign in with OIDC"
# a sign-in started and not finished within this is dropped
FLOW_MAX_AGE = 10 * 60
# clock difference allowed between soulsync and the provider
CLOCK_SKEW = 60
# discovery documents rarely change; refetching on every sign-in would put
# the provider on the critical path twice per login
DISCOVERY_TTL = 60 * 60

_discovery_lock = threading.Lock()
_discovery_cache: Dict[str, tuple] = {}
# find-or-create a profile is one step: two callbacks finishing at once must
# not make two profiles for one account
_sign_in_lock = threading.Lock()


class OidcError(Exception):
    """a sign-in that can't go on. the message is safe to show the user"""


# -- settings --

@dataclass
class OidcSettings:
    issuer: str
    client_id: str
    client_secret: str
    scopes: str = DEFAULT_SCOPES
    groups_claim: str = DEFAULT_GROUPS_CLAIM
    required_group: str = ""
    button_label: str = DEFAULT_BUTTON_LABEL
    redirect_uri: str = ""
    icon_url: str = ""


def read_settings(config_manager) -> Optional[OidcSettings]:
    """the provider settings, or None when oidc sign-in is off or not filled in"""
    if not config_manager or not config_manager.get("security.oidc_signin", False):
        return None
    issuer = (config_manager.get("security.oidc_issuer", "") or "").strip()
    client_id = (config_manager.get("security.oidc_client_id", "") or "").strip()
    client_secret = config_manager.get("security.oidc_client_secret", "") or ""
    if not issuer or not client_id or not client_secret:
        return None
    return OidcSettings(
        issuer=issuer,
        client_id=client_id,
        client_secret=client_secret,
        scopes=(config_manager.get("security.oidc_scopes", "") or "").strip() or DEFAULT_SCOPES,
        groups_claim=(config_manager.get("security.oidc_groups_claim", "") or "").strip() or DEFAULT_GROUPS_CLAIM,
        required_group=(config_manager.get("security.oidc_required_group", "") or "").strip(),
        button_label=(config_manager.get("security.oidc_button_label", "") or "").strip() or DEFAULT_BUTTON_LABEL,
        redirect_uri=(config_manager.get("security.oidc_redirect_uri", "") or "").strip(),
        icon_url=safe_icon_url(config_manager.get("security.oidc_icon_url", "") or ""),
    )


def safe_icon_url(url: str) -> str:
    """the admin's logo url, or "" unless it is plain https. it's drawn as an
    image in my account, so no other scheme gets through"""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.netloc and not any(c in url for c in "\"'<> "):
        return url
    return ""


def _is_loopback(host: str) -> bool:
    return host in ("localhost", "127.0.0.1", "::1")


def require_secure(url: str, what: str) -> None:
    """https only, since tls is what vouches for the id token. loopback http
    is let through so the flow can be tried against a local provider"""
    parsed = urlparse(url or "")
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and _is_loopback(parsed.hostname or ""):
        return
    raise OidcError(f"The provider's {what} must use https")


# -- discovery --

def _issuer_key(issuer: str) -> str:
    return (issuer or "").rstrip("/")


def discover(issuer: str, *, now: Optional[float] = None) -> Dict[str, Any]:
    """the provider's discovery document. raises OidcError when it can't be
    read or doesn't belong to this issuer"""
    now = time.time() if now is None else now
    key = _issuer_key(issuer)
    with _discovery_lock:
        hit = _discovery_cache.get(key)
        if hit and now - hit[0] < DISCOVERY_TTL:
            return hit[1]
    require_secure(key, "issuer url")
    try:
        resp = requests.get(f"{key}/.well-known/openid-configuration", timeout=TIMEOUT,
                            headers={"Accept": "application/json"})
        resp.raise_for_status()
        doc = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("OIDC discovery failed for %s: %s", key, e)
        raise OidcError("Couldn't reach the sign-in provider. Try again in a moment.") from e
    if not isinstance(doc, dict) or _issuer_key(doc.get("issuer", "")) != key:
        # a document for some other issuer would let that issuer's tokens in
        raise OidcError("The sign-in provider's settings don't match its issuer url")
    for name in ("authorization_endpoint", "token_endpoint"):
        if not doc.get(name):
            raise OidcError("The sign-in provider's discovery document is incomplete")
    require_secure(doc["token_endpoint"], "token endpoint")
    with _discovery_lock:
        _discovery_cache[key] = (now, doc)
    return doc


def forget_discovery() -> None:
    """drop cached discovery documents (settings changed, or in tests)"""
    with _discovery_lock:
        _discovery_cache.clear()


# -- starting a sign-in --

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass
class Flow:
    """what a started sign-in keeps in the browser's session until it comes back"""
    state: str
    nonce: str
    verifier: str
    redirect_uri: str
    mode: str = "signin"          # or "connect"
    profile_id: Optional[int] = None
    started_at: float = 0.0

    def to_session(self) -> Dict[str, Any]:
        return {"state": self.state, "nonce": self.nonce, "verifier": self.verifier,
                "redirect_uri": self.redirect_uri, "mode": self.mode,
                "profile_id": self.profile_id, "started_at": self.started_at}

    @classmethod
    def from_session(cls, data: Any) -> Optional["Flow"]:
        if not isinstance(data, dict):
            return None
        try:
            return cls(state=str(data["state"]), nonce=str(data["nonce"]),
                       verifier=str(data["verifier"]), redirect_uri=str(data["redirect_uri"]),
                       mode=str(data.get("mode") or "signin"),
                       profile_id=data.get("profile_id"),
                       started_at=float(data.get("started_at") or 0))
        except (KeyError, TypeError, ValueError):
            return None


def start(settings: OidcSettings, doc: Dict[str, Any], redirect_uri: str, *,
          mode: str = "signin", profile_id: Optional[int] = None,
          now: Optional[float] = None) -> tuple:
    """(the provider url to send the browser to, the Flow to keep in its session)"""
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    flow = Flow(state=secrets.token_urlsafe(32), nonce=secrets.token_urlsafe(32),
                verifier=verifier, redirect_uri=redirect_uri, mode=mode,
                profile_id=profile_id, started_at=time.time() if now is None else now)
    params = {
        "response_type": "code",
        "client_id": settings.client_id,
        "redirect_uri": redirect_uri,
        "scope": settings.scopes,
        "state": flow.state,
        "nonce": flow.nonce,
        # sent whether or not discovery advertises it: providers that don't
        # support pkce ignore unknown parameters
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    endpoint = doc["authorization_endpoint"]
    sep = "&" if "?" in endpoint else "?"
    return endpoint + sep + urlencode(params), flow


def check_callback(flow: Optional[Flow], state: str, *, now: Optional[float] = None) -> None:
    """the callback belongs to the sign-in this browser started, and in time"""
    now = time.time() if now is None else now
    if flow is None:
        raise OidcError("Sign-in expired. Try again.")
    if not state or not secrets.compare_digest(str(state), flow.state):
        raise OidcError("Sign-in expired. Try again.")
    if now - flow.started_at > FLOW_MAX_AGE:
        raise OidcError("Sign-in expired. Try again.")


# -- finishing a sign-in --

def _token_auth_method(doc: Dict[str, Any]) -> str:
    """client_secret_basic unless the provider only takes the secret in the body.
    the spec's default when the field is absent is basic"""
    methods = doc.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    if "client_secret_basic" in methods:
        return "client_secret_basic"
    if "client_secret_post" in methods:
        return "client_secret_post"
    raise OidcError("The sign-in provider needs a client authentication SoulSync doesn't support")


def exchange_code(settings: OidcSettings, doc: Dict[str, Any], code: str, flow: Flow) -> Dict[str, Any]:
    """swap the one-time code for tokens, server to server"""
    data = {"grant_type": "authorization_code", "code": code,
            "redirect_uri": flow.redirect_uri, "code_verifier": flow.verifier}
    auth = None
    if _token_auth_method(doc) == "client_secret_basic":
        auth = (settings.client_id, settings.client_secret)
    else:
        data["client_id"] = settings.client_id
        data["client_secret"] = settings.client_secret
    require_secure(doc["token_endpoint"], "token endpoint")
    try:
        resp = requests.post(doc["token_endpoint"], data=data, auth=auth, timeout=TIMEOUT,
                             headers={"Accept": "application/json"})
    except requests.RequestException as e:
        logger.warning("OIDC token exchange failed: %s", e)
        raise OidcError("Couldn't reach the sign-in provider. Try again in a moment.") from e
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code != 200 or not isinstance(body, dict) or not body.get("id_token"):
        # the provider's own error code is useful to the admin, the description
        # can carry anything, so only the code is logged
        logger.warning("OIDC token exchange refused: HTTP %s %s", resp.status_code,
                       body.get("error", "") if isinstance(body, dict) else "")
        raise OidcError("The sign-in provider refused the sign-in. Try again.")
    return body


def _decode_segment(seg: str) -> Dict[str, Any]:
    pad = "=" * (-len(seg) % 4)
    return json.loads(base64.urlsafe_b64decode(seg + pad))


def id_token_claims(id_token: str, settings: OidcSettings, doc: Dict[str, Any], nonce: str, *,
                    now: Optional[float] = None) -> Dict[str, Any]:
    """the id token's claims after the checks that don't need its signature
    (see the module doc for why the signature isn't one of them)"""
    claims = _checked_claims(id_token, settings, doc, now=now)
    if not nonce or not secrets.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise OidcError("Sign-in expired. Try again.")
    return claims


def _checked_claims(id_token: str, settings: OidcSettings, doc: Dict[str, Any], *,
                    now: Optional[float] = None) -> Dict[str, Any]:
    """issuer, audience, expiry and subject. everything id_token_claims
    checks but the nonce, which a refreshed id token doesn't carry"""
    now = time.time() if now is None else now
    try:
        parts = str(id_token).split(".")
        if len(parts) != 3:
            raise ValueError("not a jwt")
        claims = _decode_segment(parts[1])
        if not isinstance(claims, dict):
            raise ValueError("claims not an object")
    except (ValueError, TypeError) as e:
        raise OidcError("The sign-in provider sent an unreadable token") from e

    if _issuer_key(claims.get("iss", "")) != _issuer_key(doc.get("issuer", "")):
        raise OidcError("The sign-in token came from the wrong provider")
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if settings.client_id not in audiences:
        raise OidcError("The sign-in token wasn't meant for SoulSync")
    if len(audiences) > 1 and claims.get("azp") not in (None, settings.client_id):
        raise OidcError("The sign-in token wasn't meant for SoulSync")
    try:
        exp = float(claims["exp"])
    except (KeyError, TypeError, ValueError) as e:
        raise OidcError("The sign-in token has no expiry") from e
    if now > exp + CLOCK_SKEW:
        raise OidcError("The sign-in token has expired. Try again.")
    if not claims.get("sub"):
        raise OidcError("The sign-in token doesn't say who you are")
    return claims


def userinfo(doc: Dict[str, Any], access_token: str) -> Dict[str, Any]:
    """the userinfo endpoint's claims, or {} when there is none or it fails.
    only used for claims the id token left out (groups, mostly)"""
    endpoint = doc.get("userinfo_endpoint")
    if not endpoint or not access_token:
        return {}
    try:
        require_secure(endpoint, "userinfo endpoint")
        resp = requests.get(endpoint, timeout=TIMEOUT, headers={
            "Accept": "application/json", "Authorization": f"Bearer {access_token}"})
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except (requests.RequestException, ValueError, OidcError) as e:
        logger.warning("OIDC userinfo failed: %s", e)
        return {}


# -- keeping a session to the provider's lifetime (#1838) --

# a provider that hands out very short lifetimes would otherwise be asked
# again on nearly every request
MIN_SESSION = 30


def token_expiry(tokens: Dict[str, Any], claims: Optional[Dict[str, Any]], *,
                 now: Optional[float] = None) -> Optional[float]:
    """when a session signed in (or renewed) with these tokens has to be
    renewed: the access token's lifetime, else the id token's expiry. None
    when the provider said neither"""
    now = time.time() if now is None else now
    try:
        lifetime = float(tokens.get("expires_in"))
        if lifetime > 0:
            return now + max(lifetime, MIN_SESSION)
    except (TypeError, ValueError):
        pass
    try:
        exp = float((claims or {})["exp"])
        return max(exp, now + MIN_SESSION)
    except (KeyError, TypeError, ValueError):
        return None


@dataclass
class Renewal:
    expires_at: float
    refresh_token: Optional[str] = None   # None: the provider didn't rotate it


def _identity_sub(identity: str, doc: Dict[str, Any]) -> str:
    """the subject a stored identity is for, or "" when it is for another issuer"""
    issuer, _, sub = str(identity or "").rpartition("|")
    return sub if issuer and issuer == _issuer_key(doc.get("issuer", "")) else ""


def refresh(settings: OidcSettings, doc: Dict[str, Any], refresh_token: str, identity: str, *,
            now: Optional[float] = None) -> Renewal:
    """renew a session server to server. the renewal has to be for the same
    account at the same provider, and the account still has to be in the
    required group. raises OidcError when it can't be renewed, whether the
    provider refused or couldn't be reached: either way the session ends"""
    now = time.time() if now is None else now
    sub = _identity_sub(identity, doc)
    if not sub or not refresh_token:
        raise OidcError("The session can't be renewed")
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    auth = None
    if _token_auth_method(doc) == "client_secret_basic":
        auth = (settings.client_id, settings.client_secret)
    else:
        data["client_id"] = settings.client_id
        data["client_secret"] = settings.client_secret
    require_secure(doc["token_endpoint"], "token endpoint")
    try:
        resp = requests.post(doc["token_endpoint"], data=data, auth=auth, timeout=TIMEOUT,
                             headers={"Accept": "application/json"})
    except requests.RequestException as e:
        logger.warning("OIDC session renewal failed: %s", e)
        raise OidcError("Couldn't reach the sign-in provider") from e
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code != 200 or not isinstance(body, dict) or not body.get("access_token"):
        logger.info("OIDC session renewal refused: HTTP %s %s", resp.status_code,
                    body.get("error", "") if isinstance(body, dict) else "")
        raise OidcError("The sign-in provider refused to renew the session")

    claims: Optional[Dict[str, Any]] = None
    if body.get("id_token"):
        claims = _checked_claims(body["id_token"], settings, doc, now=now)
        if str(claims.get("sub")) != sub:
            raise OidcError("The renewed session is for another account")
    if settings.required_group:
        merged: Dict[str, Any] = dict(claims or {})
        if settings.groups_claim not in merged:
            extra = userinfo(doc, body["access_token"])
            # userinfo counts only for the same subject, as at sign-in
            if str(extra.get("sub") or "") != sub:
                raise OidcError("Couldn't check the account's groups")
            merged.update(extra)
        if settings.required_group not in _groups_from(merged.get(settings.groups_claim)):
            raise OidcError("The account is no longer in the required group")
    expires_at = token_expiry(body, claims, now=now)
    if expires_at is None:
        raise OidcError("The sign-in provider didn't say how long the session lasts")
    rotated = body.get("refresh_token")
    return Renewal(expires_at=expires_at,
                   refresh_token=rotated if isinstance(rotated, str) and rotated else None)


# -- who signed in --

@dataclass
class OidcAccount:
    issuer: str
    sub: str
    username: str
    groups: List[str] = field(default_factory=list)

    @property
    def identity(self) -> str:
        """what a profile records to be signed in as this account"""
        return f"{_issuer_key(self.issuer)}|{self.sub}"


def _groups_from(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(g) for g in value if isinstance(g, (str, int))]
    return []


def account_from_claims(claims: Dict[str, Any], doc: Dict[str, Any], settings: OidcSettings,
                        extra: Optional[Dict[str, Any]] = None) -> OidcAccount:
    """the signed-in account. ``extra`` is userinfo, trusted only when it is
    about the same subject as the id token"""
    merged = dict(claims)
    if extra and str(extra.get("sub") or "") == str(claims.get("sub")):
        for k, v in extra.items():
            merged.setdefault(k, v)
    name = (merged.get("preferred_username") or merged.get("name")
            or (str(merged.get("email") or "").split("@")[0]) or "OIDC user")
    return OidcAccount(issuer=str(doc.get("issuer") or settings.issuer), sub=str(claims["sub"]),
                       username=str(name), groups=_groups_from(merged.get(settings.groups_claim)))


def needs_userinfo(claims: Dict[str, Any], settings: OidcSettings) -> bool:
    """the required-group check can't be made from the id token alone"""
    return bool(settings.required_group) and settings.groups_claim not in claims


# -- mapping an account to a profile --

@dataclass
class SignInResult:
    profile_id: Optional[int] = None
    error: Optional[str] = None
    created: bool = False


def allowed_in(account: OidcAccount, settings: OidcSettings) -> bool:
    return not settings.required_group or settings.required_group in account.groups


def sign_in(db, account: OidcAccount, settings: OidcSettings, *,
            allow_create: bool, default_can_download: bool,
            access: Optional[SignupAccess] = None) -> SignInResult:
    """map a signed-in oidc account to a soulsync profile (see module doc).
    `access` is what a new profile starts with (signup_defaults.signup_access);
    without one, the shipped default"""
    with _sign_in_lock:
        return _sign_in(db, account, settings, allow_create=allow_create,
                        default_can_download=default_can_download, access=access or SignupAccess())


def _sign_in(db, account: OidcAccount, settings: OidcSettings, *,
             allow_create: bool, default_can_download: bool, access: SignupAccess) -> SignInResult:
    if not allowed_in(account, settings):
        return SignInResult(error="Your account isn't allowed to use SoulSync. Ask your admin.")

    profile = db.get_profile_by_oidc_identity(account.identity)
    if profile:
        if profile.get("disabled") and not profile.get("is_admin"):
            return SignInResult(error="This profile is turned off. Ask your admin.")
        # an admin profile is reachable only because a signed-in admin
        # connected this account to it from my account (connect_profile)
        return SignInResult(profile_id=profile["id"])

    if not allow_create:
        return SignInResult(error="Your account isn't linked to a SoulSync profile yet. Ask your admin.")

    profile_id = _create_profile(db, account.username, can_download=default_can_download, access=access)
    if profile_id is None:
        return SignInResult(error="Couldn't create a profile for your account")
    db.set_profile_oidc_identity(profile_id, account.identity)
    logger.info("OIDC sign-in: created profile %s for '%s'", profile_id, account.username)
    return SignInResult(profile_id=profile_id, created=True)


def connect_profile(db, profile_id: int, account: OidcAccount) -> SignInResult:
    """connect from my account: link a signed-in profile to the oidc account
    it just proved at the provider. after this, oidc sign-in lands on it"""
    with _sign_in_lock:
        holder = db.get_profile_by_oidc_identity(account.identity)
        if holder and holder.get("id") != profile_id:
            # one account, one profile: sign-in has to know where it lands
            return SignInResult(error="That account is connected to another SoulSync profile")
        if not db.set_profile_oidc_identity(profile_id, account.identity):
            return SignInResult(error="Couldn't save the connection")
        logger.info("OIDC connect: profile %s connected", profile_id)
        return SignInResult(profile_id=profile_id)


def _create_profile(db, username: str, *, can_download: bool,
                    access: Optional[SignupAccess] = None) -> Optional[int]:
    """a profile named after the account; a taken name gets a number.
    starts with the same access as any sign-up (signup_defaults)"""
    from core.security.plex_signin import profile_name_for
    access = access or SignupAccess()
    base = profile_name_for(username)
    if base == "Plex user":
        base = "OIDC user"
    for n in range(0, 50):
        name = base if n == 0 else f"{base} {n + 1}"
        if db.get_profile_by_name(name):
            continue
        pid = create_signup_profile(db, name, access, can_download=can_download)
        if pid:
            return pid
    return None

"""Static descriptors for the media servers SoulSync can talk to.

One record per server holding the facts that used to live as literals in
several places: the list of valid server names (``settings.py``), which
config section and keys each server needs and which of them make it
"configured" (``settings.py`` twice, the settings page), the default port
and probe for auto-detect (``connection_detect.py``), the display name
(``registry.py``), and whether the server supports per-profile own
libraries (``library_scope.py``, ``user_profiles.py``, ``database_admin.py``).

Pure data. This module imports nothing from the client modules, so
``core.settings`` can read it without the circular import that
``registry.py`` would cause (the registry imports every client class and
every client imports ``config_manager``).

Adding a server: one ``ServerDescriptor`` here, one ``register`` call in
``registry.py``, and the client class.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


@dataclass(frozen=True)
class ConfigField:
    """One key in a server's config section.

    ``kind`` is a hint for the settings form and for redaction:
    ``url``, ``token``, ``username``, ``password``, ``path`` or ``text``.
    ``required`` fields are the ones that decide "is this server
    configured"; the rest are optional tuning.
    """

    key: str
    kind: str = 'text'
    required: bool = True
    label: str = ''
    default: Any = None

    @property
    def secret(self) -> bool:
        return self.kind in ('token', 'password')


@dataclass(frozen=True)
class Detection:
    """How auto-detect looks for this server on the LAN: the default port
    and the HTTP path that answers when the server is up."""

    port: int
    path: str = '/'


@dataclass(frozen=True)
class Brand:
    """What the UI shows for the server. ``dark`` means the logo wants a
    dark backdrop."""

    color: str
    logo: str
    dark: bool = False


@dataclass(frozen=True)
class ServerDescriptor:
    name: str
    display_name: str
    # Config section in config.json. None means the server reads no
    # section of its own.
    config_section: Optional[str]
    fields: Tuple[ConfigField, ...] = ()
    detection: Optional[Detection] = None
    brand: Optional[Brand] = None
    # What the user picks inside the server: a Plex section, a Jellyfin
    # library, a Navidrome music folder, or nothing.
    library_concept: str = 'none'
    # Per-profile own libraries (#1199) need a server that can hold more
    # than one music library. Plex and Jellyfin can; Navidrome has one
    # music folder per server; the standalone library has no server.
    supports_own_libraries: bool = False
    # The server has user accounts a profile can be linked to.
    per_user_accounts: bool = False
    # A real media server, as opposed to SoulSync reading its own files.
    is_external: bool = True
    aliases: Tuple[str, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------
    # Config helpers. Every caller used to spell these out per server.
    # ------------------------------------------------------------------

    @property
    def required_keys(self) -> Tuple[str, ...]:
        return tuple(f.key for f in self.fields if f.required)

    @property
    def secret_keys(self) -> Tuple[str, ...]:
        return tuple(f.key for f in self.fields if f.secret)

    def config_from(self, config_manager) -> Dict[str, Any]:
        """The server's config as the old ``get_<server>_config`` getters
        returned it: the whole section for a server that owns one, or
        the named keys with their defaults for a server that borrows
        keys from another section."""
        if not self.config_section:
            return {}
        if self.name == self.config_section:
            return config_manager.get(self.config_section, {}) or {}
        out: Dict[str, Any] = {}
        for f in self.fields:
            out[f.key] = config_manager.get(f'{self.config_section}.{f.key}', f.default)
        return out

    def is_configured(self, config: Mapping[str, Any]) -> bool:
        """Every required key present and non-empty. A server with no
        required keys is always configured (the standalone library)."""
        return all(bool(config.get(k)) for k in self.required_keys)

    def is_configured_in(self, config_manager) -> bool:
        return self.is_configured(self.config_from(config_manager))

    def to_public_dict(self) -> Dict[str, Any]:
        """Shape served to the UI. Field kinds are included so a form
        can be rendered from it; values never are."""
        return {
            'name': self.name,
            'display_name': self.display_name,
            'config_section': self.config_section,
            'fields': [
                {'key': f.key, 'kind': f.kind, 'required': f.required,
                 'label': f.label or f.key.replace('_', ' ').title()}
                for f in self.fields
            ],
            'detection': (
                {'port': self.detection.port, 'path': self.detection.path}
                if self.detection else None
            ),
            'brand': (
                {'color': self.brand.color, 'logo': self.brand.logo, 'dark': self.brand.dark}
                if self.brand else None
            ),
            'library_concept': self.library_concept,
            'supports_own_libraries': self.supports_own_libraries,
            'per_user_accounts': self.per_user_accounts,
            'is_external': self.is_external,
        }


# ---------------------------------------------------------------------------
# The servers. Order is the order the UI shows them in.
# ---------------------------------------------------------------------------

PLEX = ServerDescriptor(
    name='plex',
    display_name='Plex',
    config_section='plex',
    fields=(
        ConfigField('base_url', 'url', label='Plex URL'),
        ConfigField('token', 'token', label='X-Plex-Token'),
        ConfigField('auto_detect', 'text', required=False, default=True),
        ConfigField('request_timeout_seconds', 'text', required=False, default=30),
        ConfigField('scan_retries', 'text', required=False, default=2),
    ),
    detection=Detection(port=32400, path='/web/index.html'),
    brand=Brand(color='#e5a00d', logo='/static/img/brands/plex.png', dark=True),
    library_concept='section',
    supports_own_libraries=True,
    per_user_accounts=True,
)

JELLYFIN = ServerDescriptor(
    name='jellyfin',
    display_name='Jellyfin',
    config_section='jellyfin',
    fields=(
        ConfigField('base_url', 'url', label='Jellyfin URL'),
        ConfigField('api_key', 'token', label='API key'),
        ConfigField('auto_detect', 'text', required=False, default=True),
    ),
    detection=Detection(port=8096, path='/System/Info/Public'),
    brand=Brand(color='#aa5cc3', logo='/static/img/brands/jellyfin.png'),
    library_concept='library',
    supports_own_libraries=True,
    per_user_accounts=True,
)

NAVIDROME = ServerDescriptor(
    name='navidrome',
    display_name='Navidrome',
    config_section='navidrome',
    fields=(
        ConfigField('base_url', 'url', label='Navidrome URL'),
        ConfigField('username', 'username', label='Username'),
        ConfigField('password', 'password', label='Password'),
        ConfigField('auto_detect', 'text', required=False, default=True),
    ),
    detection=Detection(port=4533, path='/rest/ping'),
    brand=Brand(color='#3b6cf6', logo='/static/img/brands/navidrome.png'),
    library_concept='folder',
    per_user_accounts=True,
)

SOULSYNC = ServerDescriptor(
    name='soulsync',
    display_name='SoulSync Library',
    # No section of its own: the standalone library reads the transfer
    # folder from the soulseek section.
    config_section='soulseek',
    fields=(
        ConfigField('transfer_path', 'path', required=False, label='Transfer folder', default='./Transfer'),
    ),
    brand=Brand(color='#7c5cff', logo='/static/trans2.png', dark=True),
    is_external=False,
)

SERVERS: Tuple[ServerDescriptor, ...] = (PLEX, JELLYFIN, NAVIDROME, SOULSYNC)

_BY_NAME: Dict[str, ServerDescriptor] = {}
for _d in SERVERS:
    _BY_NAME[_d.name] = _d
    for _a in _d.aliases:
        _BY_NAME[_a] = _d
del _d


def describe(name: Optional[str]) -> Optional[ServerDescriptor]:
    """Descriptor for a server name or alias, None if unknown."""
    if not name:
        return None
    return _BY_NAME.get(str(name).lower())


def names() -> List[str]:
    """Canonical server names, in UI order."""
    return [d.name for d in SERVERS]


def external_names() -> List[str]:
    """Names of the real media servers (everything but the standalone
    library)."""
    return [d.name for d in SERVERS if d.is_external]


def names_where(**flags: bool) -> List[str]:
    """Names of the servers whose descriptor flags match, e.g.
    ``names_where(supports_own_libraries=True)``."""
    out = []
    for d in SERVERS:
        if all(getattr(d, k) == v for k, v in flags.items()):
            out.append(d.name)
    return out


def is_known(name: Optional[str]) -> bool:
    return describe(name) is not None


def display_name(name: str) -> str:
    d = describe(name)
    return d.display_name if d else str(name)


def iter_descriptors() -> Iterable[ServerDescriptor]:
    return iter(SERVERS)

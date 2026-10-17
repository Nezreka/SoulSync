"""Media server descriptors for the UI.

``GET /api/media-servers`` lists every server SoulSync knows about, with
the facts the settings page, the setup wizard and the quick-switch need
to render a server without a hand-written block per server: display
name, config fields (kinds only, never values), default detection port,
brand, library concept, and whether it is configured and active right
now.

Read-only. Injected deps are web_server's boot singletons."""

from __future__ import annotations

from flask import Blueprint, jsonify

from core.media_server import specs
from utils.logging_config import get_logger

logger = get_logger("api.media_servers")

bp = Blueprint("media_servers", __name__)

# Injected by configure() at boot.
config_manager = None
_engine = lambda: None  # noqa: E731


def configure(*, config_manager_, engine_getter=None):
    global config_manager, _engine
    config_manager = config_manager_
    if engine_getter is not None:
        _engine = engine_getter


def _capabilities(name: str):
    """Declared capabilities from the live client, [] when none is wired."""
    engine = _engine()
    client = engine.client(name) if engine is not None else None
    return sorted(getattr(client, 'CAPABILITIES', None) or ())


def _connected(name: str):
    """Connection state from the engine when one is wired, else None."""
    engine = _engine()
    if engine is None:
        return None
    try:
        client = engine.client(name)
        return bool(client.is_connected()) if client is not None else False
    except Exception as exc:  # noqa: BLE001 - a dead server must not break the listing
        logger.debug("%s is_connected raised in /api/media-servers: %s", name, exc)
        return False


def describe_all(include_state: bool = True):
    active = config_manager.get_active_media_server() if config_manager else None
    out = []
    for d in specs.iter_descriptors():
        item = d.to_public_dict()
        if include_state and config_manager is not None:
            item['configured'] = d.is_configured_in(config_manager)
            item['active'] = (d.name == active)
            item['connected'] = _connected(d.name)
            item['capabilities'] = _capabilities(d.name)
        out.append(item)
    return {'active': active, 'servers': out}


@bp.route('/api/media-servers', methods=['GET'])
def list_media_servers():
    return jsonify(describe_all())


def create_blueprint():
    return bp

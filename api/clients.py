"""Download-client hub endpoints - the Clients tab on the downloads page.

one place to see and unstick everything the external clients are doing:
the torrent client (qbittorrent/transmission/deluge/aria2), the usenet
client (sabnzbd/nzbget), and slskd itself. the adapters already speak
every verb this needs - these routes are a thin sync bridge over them.

scope is deliberately "see what's happening and unstick it": list,
pause, resume, remove/cancel. building a whole client ui is the
client's job.
"""

import asyncio
from dataclasses import asdict

from flask import Blueprint, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("api.clients")

# injected by configure()
config_manager = None
_soulseek_client = None
_known_items = None


def configure(*, config_manager_, soulseek_client_getter, known_items_getter=None):
    """known_items_getter() -> {'torrent': {id: {...}}, 'usenet': {id: {...}},
    'slskd': {(username, filename): {...}}} - what SoulSync itself dispatched,
    so rows the app owns can say what they ARE. optional; None means nothing
    gets labeled."""
    global config_manager, _soulseek_client, _known_items
    config_manager = config_manager_
    _soulseek_client = soulseek_client_getter
    _known_items = known_items_getter


def _run(coro, timeout=25):
    """Run an async adapter call from sync flask code on a throwaway loop."""
    async def _capped():
        return await asyncio.wait_for(coro, timeout=timeout)
    return asyncio.run(_capped())


def _json_guard(fn):
    """Every route failure leaves as json with a message. The first version
    let a broken getter escape as flask's html 500 page, and the ui could
    only show that raw."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            logger.error(f"[Clients] {fn.__name__} failed: {e}", exc_info=True)
            return jsonify({"success": False, "error": str(e)}), 500
    return wrapper


def _known(kind):
    try:
        return (_known_items() or {}).get(kind, {}) if _known_items else {}
    except Exception as exc:
        logger.debug(f"[Clients] known-items lookup failed: {exc}")
        return {}


def create_blueprint() -> Blueprint:
    bp = Blueprint('clients', __name__)

    # ── torrent ───────────────────────────────────────────────────────────

    @bp.route('/api/clients/torrent', methods=['GET'])
    @_json_guard
    def torrent_overview():
        from core.torrent_clients import get_active_adapter
        client_type = (config_manager.get('torrent_client.type', '') or '').strip().lower()
        adapter = get_active_adapter()
        if not adapter or not adapter.is_configured():
            return jsonify({"success": True, "configured": False, "type": client_type,
                            "connected": False, "items": []})
        try:
            items = _run(adapter.get_all())
        except Exception as e:
            logger.warning(f"[Clients] torrent get_all failed: {e}")
            return jsonify({"success": True, "configured": True, "type": client_type,
                            "connected": False, "error": str(e), "items": []})
        known = _known('torrent')
        rows = []
        for t in items:
            row = asdict(t)
            row.pop('files', None)      # can be huge; the list view doesn't need it
            hit = known.get(str(t.id).lower())
            if hit:
                row['soulsync'] = hit
            rows.append(row)
        return jsonify({"success": True, "configured": True, "type": client_type,
                        "connected": True, "items": rows})

    @bp.route('/api/clients/torrent/action', methods=['POST'])
    @_json_guard
    def torrent_action():
        from core.torrent_clients import get_active_adapter
        payload = request.get_json(silent=True) or {}
        item_id = str(payload.get('id') or '').strip()
        action = str(payload.get('action') or '').strip()
        if not item_id or action not in ('pause', 'resume', 'remove'):
            return jsonify({"success": False, "error": "id and a valid action required"}), 400
        adapter = get_active_adapter()
        if not adapter or not adapter.is_configured():
            return jsonify({"success": False, "error": "no torrent client configured"}), 400
        try:
            if action == 'pause':
                ok = _run(adapter.pause(item_id))
            elif action == 'resume':
                ok = _run(adapter.resume(item_id))
            else:
                ok = _run(adapter.remove(item_id, delete_files=bool(payload.get('delete_files'))))
            return jsonify({"success": bool(ok)} if ok else
                           {"success": False, "error": f"{action} was refused by the client"})
        except Exception as e:
            logger.warning(f"[Clients] torrent {action} failed for {item_id}: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    # ── usenet ────────────────────────────────────────────────────────────

    @bp.route('/api/clients/usenet', methods=['GET'])
    @_json_guard
    def usenet_overview():
        from core.usenet_clients import get_active_adapter
        client_type = (config_manager.get('usenet_client.type', '') or '').strip().lower()
        adapter = get_active_adapter()
        if not adapter or not adapter.is_configured():
            return jsonify({"success": True, "configured": False, "type": client_type,
                            "connected": False, "items": []})
        try:
            items = _run(adapter.get_all())
        except Exception as e:
            logger.warning(f"[Clients] usenet get_all failed: {e}")
            return jsonify({"success": True, "configured": True, "type": client_type,
                            "connected": False, "error": str(e), "items": []})
        known = _known('usenet')
        rows = []
        for j in items:
            row = asdict(j)
            row.pop('files', None)
            hit = known.get(str(j.id))
            if hit:
                row['soulsync'] = hit
            rows.append(row)
        return jsonify({"success": True, "configured": True, "type": client_type,
                        "connected": True, "items": rows})

    @bp.route('/api/clients/usenet/action', methods=['POST'])
    @_json_guard
    def usenet_action():
        from core.usenet_clients import get_active_adapter
        payload = request.get_json(silent=True) or {}
        item_id = str(payload.get('id') or '').strip()
        action = str(payload.get('action') or '').strip()
        if not item_id or action not in ('pause', 'resume', 'remove'):
            return jsonify({"success": False, "error": "id and a valid action required"}), 400
        adapter = get_active_adapter()
        if not adapter or not adapter.is_configured():
            return jsonify({"success": False, "error": "no usenet client configured"}), 400
        try:
            if action == 'pause':
                ok = _run(adapter.pause(item_id))
            elif action == 'resume':
                ok = _run(adapter.resume(item_id))
            else:
                ok = _run(adapter.remove(item_id, delete_files=bool(payload.get('delete_files'))))
            return jsonify({"success": bool(ok)} if ok else
                           {"success": False, "error": f"{action} was refused by the client"})
        except Exception as e:
            logger.warning(f"[Clients] usenet {action} failed for {item_id}: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    # ── slskd ─────────────────────────────────────────────────────────────

    @bp.route('/api/clients/slskd', methods=['GET'])
    @_json_guard
    def slskd_overview():
        # the whole body is guarded: the first version let a broken getter
        # escape as flask's html 500, which the ui can only show raw. every
        # failure from here leaves as json with a message.
        try:
            client = _soulseek_client() if _soulseek_client else None
            configured = False
            if client is not None:
                try:
                    configured = bool(client.is_configured())
                except Exception:
                    configured = bool(getattr(client, 'base_url', ''))
            if not client or not configured:
                return jsonify({"success": True, "configured": False, "connected": False,
                                "items": []})
            try:
                items = _run(client.get_all_downloads())
            except Exception as e:
                logger.warning(f"[Clients] slskd get_all_downloads failed: {e}")
                return jsonify({"success": True, "configured": True, "connected": False,
                                "error": str(e), "items": []})
            known = _known('slskd')
            rows = []
            for d in items:
                row = asdict(d)
                row.pop('audio_files', None)
                hit = known.get((d.username, d.filename))
                if hit:
                    row['soulsync'] = hit
                rows.append(row)
            logger.debug(f"[Clients] slskd listing: {len(rows)} transfers")
            return jsonify({"success": True, "configured": True, "connected": True,
                            "items": rows})
        except Exception as e:
            logger.error(f"[Clients] slskd overview failed: {e}", exc_info=True)
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route('/api/clients/slskd/action', methods=['POST'])
    @_json_guard
    def slskd_action():
        client = _soulseek_client() if _soulseek_client else None
        payload = request.get_json(silent=True) or {}
        item_id = str(payload.get('id') or '').strip()
        username = str(payload.get('username') or '').strip()
        action = str(payload.get('action') or '').strip()
        if not item_id or action != 'cancel':
            return jsonify({"success": False, "error": "id and action 'cancel' required"}), 400
        if not client:
            return jsonify({"success": False, "error": "slskd is not configured"}), 400
        try:
            ok = _run(client.cancel_download(item_id, username or None,
                                            remove=bool(payload.get('remove'))))
            return jsonify({"success": bool(ok)} if ok else
                           {"success": False, "error": "cancel was refused by slskd"})
        except Exception as e:
            logger.warning(f"[Clients] slskd cancel failed for {item_id}: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    return bp

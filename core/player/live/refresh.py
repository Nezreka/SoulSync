"""Refresh a live TV source (HDHomeRun, M3U, or XMLTV) into player.db."""

from __future__ import annotations

from typing import Any, Dict

from utils.logging_config import get_logger

from . import hdhomerun, m3u, store, xmltv

logger = get_logger("player.live.refresh")


def refresh_source(db: Any, source_id: str, *, http_get=None) -> Dict[str, Any]:
    """Pull fresh channels and/or EPG programs for one live source."""
    src = store.get_source(db, source_id)
    if not src:
        raise store.LiveStoreError("Live source not found")

    kind = src["kind"]
    try:
        if kind == "hdhomerun":
            info = hdhomerun.probe_device(src["url"], http_get=http_get)
            lineup = hdhomerun.fetch_lineup(info["lineup_url"], http_get=http_get)
            ch_stats = store.upsert_channels(db, source_id, lineup)
            epg_count = 0
            if info.get("device_auth"):
                raw_xml = hdhomerun.fetch_xmltv(info["device_auth"], http_get=http_get)
                if raw_xml:
                    _, progs = xmltv.parse_xmltv(raw_xml)
                    epg_count = store.replace_programs(db, source_id, progs)
            store.update_source_status(
                db, source_id,
                last_error=None,
                device_auth=info.get("device_auth"),
                tuner_count=info.get("tuner_count"),
            )
            return {"ok": True, "kind": kind, **ch_stats, "programs": epg_count}

        if kind == "m3u":
            chans = m3u.load_m3u(src["url"], http_get=http_get)
            ch_stats = store.upsert_channels(db, source_id, chans)
            store.update_source_status(db, source_id, last_error=None)
            return {"ok": True, "kind": kind, **ch_stats, "programs": 0}

        if kind == "xmltv":
            _chans, progs = xmltv.load_xmltv(src["url"], http_get=http_get)
            epg_count = store.replace_programs(db, source_id, progs)
            store.update_source_status(db, source_id, last_error=None)
            return {"ok": True, "kind": kind, "programs": epg_count}

        raise store.LiveStoreError(f"Unsupported source kind: {kind}")
    except Exception as exc:  # noqa: BLE001
        store.update_source_status(db, source_id, last_error=str(exc))
        logger.warning("live source refresh failed (%s): %s", source_id, exc)
        return {"ok": False, "error": str(exc)}


def refresh_due_sources(
    db: Any,
    *,
    interval_hours: float = 12.0,
    now: float | None = None,
    http_get=None,
) -> Dict[str, Any]:
    """Refresh all enabled sources past ``interval_hours`` since their last refresh,
    then re-evaluate DVR recording rules so newly arrived EPG programs get scheduled."""
    import time
    from . import dvr

    ts = now if now is not None else time.time()
    min_age = max(1.0, float(interval_hours)) * 3600.0
    refreshed = 0
    errors = 0
    for src in store.list_sources(db):
        if not src.get("enabled"):
            continue
        last = float(src.get("last_refresh_at") or 0.0)
        if last > 0.0 and (ts - last) < min_age:
            continue
        res = refresh_source(db, src["id"], http_get=http_get)
        if res.get("ok"):
            refreshed += 1
        else:
            errors += 1
    dvr_stats = dvr.evaluate_rules(db, now=ts) if refreshed else {"scheduled": 0, "conflicts": 0}
    return {"refreshed": refreshed, "errors": errors, "dvr": dvr_stats}

"""Automation handler: ``metasync_refresh_charts`` action.

The daily pull of MetaSync's SIN charts (core/metasync_charts.py). Stores
and matches them; never syncs, wishlists or downloads anything.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps
from utils.logging_config import get_logger

logger = get_logger("metasync_charts_automation")


def auto_refresh_metasync_charts(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    from api.metasync_charts import identify_soon
    from core import metasync_charts

    try:
        result = metasync_charts.refresh_from_node(
            deps.get_database(), deps.config_manager.get, identify_soon)
    except Exception as e:  # noqa: BLE001 — automation handlers must never raise into the engine
        return {'status': 'error', 'error': str(e)}

    # A successful pull proves the node is reachable — that IS
    # SoulSync's reconnect moment (there is no persistent connection),
    # so the metadata depot reconciles against the node's live view
    # (DESIGN-METADATA §5): subjects the node no longer holds die in
    # the depot, live ones refresh. Best effort: a depot failure
    # never fails the charts refresh.
    if isinstance(result, dict) and result.get('status') not in ('error', 'skipped'):
        try:
            from core import metasync_depot
            if deps.config_manager.get('metasync.depot_enabled', True) is not False:
                metasync_depot.reconcile(
                    deps.get_database(), deps.config_manager.get)
        except Exception as e:  # noqa: BLE001 — best effort by design
            logger.debug(f"metasync depot reconcile skipped: {e}")
    return result

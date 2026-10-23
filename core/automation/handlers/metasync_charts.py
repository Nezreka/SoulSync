"""Automation handler: ``metasync_refresh_charts`` action.

The daily pull of MetaSync's SIN charts (core/metasync_charts.py). Stores
and matches them; never syncs, wishlists or downloads anything.
"""

from __future__ import annotations

from typing import Any, Dict

from core.automation.deps import AutomationDeps


def auto_refresh_metasync_charts(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    from api.metasync_charts import identify_soon
    from core import metasync_charts

    try:
        return metasync_charts.refresh_from_node(
            deps.get_database(), deps.config_manager.get, identify_soon)
    except Exception as e:  # noqa: BLE001 — automation handlers must never raise into the engine
        return {'status': 'error', 'error': str(e)}

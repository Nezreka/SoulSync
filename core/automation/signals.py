"""Automation signal helpers — name collection for autocomplete.

Signal cycle detection itself lives in core/automation_engine.py
(`detect_signal_cycles`); this module just enumerates known signal
names from the saved automation set so the builder UI can autocomplete.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def collect_known_signals(database) -> list[str]:
    """Return sorted, deduped signal names referenced by any saved automation.

    Walks every automation and pulls signal names from both the
    `signal_received` trigger config and any `fire_signal` then-actions.
    Errors at every layer are swallowed — the autocomplete is best-effort.
    """
    signals: set[str] = set()
    try:
        for auto in database.get_automations():
            if auto.get('trigger_type') == 'signal_received':
                try:
                    tc = json.loads(auto.get('trigger_config') or '{}')
                    sig = tc.get('signal_name', '').strip()
                    if sig:
                        signals.add(sig)
                except (json.JSONDecodeError, TypeError):
                    pass

            try:
                ta = json.loads(auto.get('then_actions') or '[]')
                for item in ta:
                    if item.get('type') == 'fire_signal':
                        sig = item.get('config', {}).get('signal_name', '').strip()
                        if sig:
                            signals.add(sig)
            except (json.JSONDecodeError, TypeError):
                pass
    except Exception as e:
        logger.debug("collect known signals failed: %s", e)
    return sorted(signals)

"""Automation API + progress + handlers package.

Lifted from web_server.py:
  - `/api/automations/*` route helpers → `api.py`
  - block library used by the trigger/action UI → `blocks.py`
  - progress tracker (init / update / finish) → `progress.py`
  - cross-handler signal bus → `signals.py`
  - per-action handler functions → `handlers/` subpackage (with
    `deps.py` defining the dependency-injection surface so handlers
    stay testable in isolation)
"""

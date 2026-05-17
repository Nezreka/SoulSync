"""Automation handler: ``run_script`` action.

Lifted from ``web_server._register_automation_handlers`` (the
``_auto_run_script`` closure). Runs a user-provided shell or Python
script from the configured scripts directory with bounded timeout +
captured stdout/stderr. Path-traversal guard ensures users can't
escape the scripts directory.

Environment variables exposed to the script:
- ``SOULSYNC_EVENT``: triggering event type (when fired by an event)
- ``SOULSYNC_AUTOMATION``: automation name
- ``SOULSYNC_SCRIPTS_DIR``: absolute path to the scripts dir
"""

from __future__ import annotations

import os
import subprocess as _sp
from typing import Any, Dict

from core.automation.deps import AutomationDeps


_MAX_TIMEOUT_SECONDS = 300  # Hard cap on user-supplied timeout config.


def auto_run_script(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    script_name = config.get('script_name', '')
    timeout = min(int(config.get('timeout', 60)), _MAX_TIMEOUT_SECONDS)
    automation_id = config.get('_automation_id')

    if not script_name:
        return {'status': 'error', 'error': 'No script selected'}

    scripts_dir = deps.docker_resolve_path(deps.config_manager.get('scripts.path', './scripts'))
    if not scripts_dir or not os.path.isdir(scripts_dir):
        os.makedirs(scripts_dir, exist_ok=True)
        return {
            'status': 'error',
            'error': 'Scripts directory is empty. Add scripts to the scripts/ folder.',
        }

    script_path = os.path.join(scripts_dir, script_name)
    script_path = os.path.realpath(script_path)

    # Security: block path traversal — script must resolve under
    # the scripts dir, no symlinks/.. tricks allowed out.
    if not script_path.startswith(os.path.realpath(scripts_dir)):
        return {'status': 'error', 'error': 'Script path traversal blocked'}

    if not os.path.isfile(script_path):
        return {'status': 'error', 'error': f'Script not found: {script_name}'}

    deps.update_progress(automation_id, phase=f'Running {script_name}...', progress=10)

    # Build environment with SoulSync context.
    env = os.environ.copy()
    event_data = config.get('_event_data') or {}
    env['SOULSYNC_EVENT'] = str(event_data.get('type', ''))
    env['SOULSYNC_AUTOMATION'] = config.get('_automation_name', '')
    env['SOULSYNC_SCRIPTS_DIR'] = scripts_dir

    try:
        # Determine how to run the script.
        if script_path.endswith('.py'):
            cmd = ['python', script_path]
        elif script_path.endswith('.sh'):
            cmd = ['bash', script_path]
        else:
            cmd = [script_path]

        result = _sp.run(
            cmd,
            capture_output=True, text=True, timeout=timeout,
            cwd=scripts_dir, env=env,
        )

        deps.update_progress(automation_id, phase='Script completed', progress=100)

        stdout = result.stdout[:2000] if result.stdout else ''
        stderr = result.stderr[:1000] if result.stderr else ''

        if result.returncode == 0:
            deps.logger.info(f"Script '{script_name}' completed (exit 0)")
        else:
            deps.logger.warning(f"Script '{script_name}' exited with code {result.returncode}")

        return {
            'status': 'completed' if result.returncode == 0 else 'error',
            'exit_code': str(result.returncode),
            'stdout': stdout,
            'stderr': stderr,
            'script': script_name,
        }
    except _sp.TimeoutExpired:
        deps.update_progress(automation_id, phase='Script timed out', progress=100)
        return {
            'status': 'error',
            'error': f'Script timed out after {timeout}s',
            'script': script_name,
        }
    except Exception as e:  # noqa: BLE001 — automation handlers must never raise
        return {'status': 'error', 'error': str(e), 'script': script_name}

"""
Automation Engine — trigger → action → then scheduler for SoulSync.

Architecture:
- Triggers (WHEN): schedule timer, event-based, signal-based (signal_received)
- Actions (DO): real SoulSync operations registered by web_server.py
- Then (THEN): 1–3 post-action steps — notifications (Discord/Pushbullet/Telegram) and/or fire_signal
- Conditions: optional filters on event data (artist contains, title equals, etc.)
- Signals: user-named events that chain automations together (fire_signal → signal_received)

Uses threading.Timer pattern for schedule triggers.
Event triggers react to emit() calls from web_server.py hook points.
"""

import json
import re
import time
import threading
import requests
from datetime import datetime, timedelta, timezone
from utils.logging_config import get_logger

logger = get_logger("automation_engine")

def _utcnow():
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)

def _utcnow_str():
    """Return current UTC time as naive string for DB storage (consistent with SQLite CURRENT_TIMESTAMP)."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

def _utc_after(seconds):
    """Return UTC time N seconds from now as naive string for DB storage."""
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime('%Y-%m-%d %H:%M:%S')

SYSTEM_AUTOMATIONS = [
    {
        'name': 'Auto-Process Wishlist',
        'trigger_type': 'schedule',
        'trigger_config': {'interval': 30, 'unit': 'minutes'},
        'action_type': 'process_wishlist',
        'initial_delay': 60,  # 1 minute after startup
    },
    {
        'name': 'Auto-Scan Watchlist',
        'trigger_type': 'schedule',
        'trigger_config': {'interval': 24, 'unit': 'hours'},
        'action_type': 'scan_watchlist',
        'initial_delay': 300,  # 5 minutes after startup
    },
    # Event-based system automations (no initial_delay/next_run needed)
    {
        'name': 'Auto-Scan After Downloads',
        'trigger_type': 'batch_complete',
        'trigger_config': {},
        'action_type': 'scan_library',
    },
    {
        'name': 'Auto-Update Database After Scan',
        'trigger_type': 'library_scan_completed',
        'trigger_config': {},
        'action_type': 'start_database_update',
    },
    # Maintenance automations
    {
        'name': 'Refresh Beatport Cache',
        'trigger_type': 'schedule',
        'trigger_config': {'interval': 24, 'unit': 'hours'},
        'action_type': 'refresh_beatport_cache',
        'initial_delay': 120,
    },
    {
        'name': 'Clean Search History',
        'trigger_type': 'schedule',
        'trigger_config': {'interval': 1, 'unit': 'hours'},
        'action_type': 'clean_search_history',
        'initial_delay': 600,
    },
    {
        'name': 'Clean Completed Downloads',
        'trigger_type': 'schedule',
        'trigger_config': {'interval': 5, 'unit': 'minutes'},
        'action_type': 'clean_completed_downloads',
        'initial_delay': 300,
    },
    {
        'name': 'Auto-Deep Scan Library',
        'trigger_type': 'schedule',
        'trigger_config': {'interval': 7, 'unit': 'days'},
        'action_type': 'deep_scan_library',
        'initial_delay': 900,  # 15 min after startup
    },
    {
        'name': 'Auto-Backup Database',
        'trigger_type': 'schedule',
        'trigger_config': {'interval': 3, 'unit': 'days'},
        'action_type': 'backup_database',
        'initial_delay': 600,  # 10 min after startup
    },
    {
        'name': 'Full Cleanup',
        'trigger_type': 'schedule',
        'trigger_config': {'interval': 12, 'unit': 'hours'},
        'action_type': 'full_cleanup',
        'initial_delay': 900,  # 15 min after startup
    },
]


class AutomationEngine:
    def __init__(self, db):
        self.db = db
        self._timers = {}       # automation_id → threading.Timer
        self._lock = threading.Lock()
        self._running = False

        # Action handlers registered by web_server.py (avoids circular imports)
        # Format: {type: {'handler': fn(config)->dict, 'guard': fn()->bool or None}}
        self._action_handlers = {}

        # Progress tracking callbacks (registered by web_server.py)
        self._progress_init_fn = None
        self._progress_finish_fn = None
        self._progress_update_fn = None
        self._history_record_fn = None

        # Event trigger cache: trigger_type → [automation_id, ...]
        self._event_automations = {}
        self._event_cache_dirty = True

        # Signal safety: cooldown tracking and chain depth limit
        self._signal_cooldowns = {}       # signal event key → last fire timestamp
        self._max_chain_depth = 5
        self._signal_cooldown_seconds = 10

        # Trigger registry: type → setup function (schedule only — events use emit())
        self._trigger_handlers = {
            'schedule': self._setup_schedule_trigger,
            'daily_time': self._setup_daily_time_trigger,
            'weekly_time': self._setup_weekly_time_trigger,
        }

    # --- Action Handler Registration ---

    def register_action_handler(self, action_type, handler_fn, guard_fn=None):
        """Register a callable for an action type.
        handler_fn(config) -> dict with result data
        guard_fn() -> bool (True = busy, should skip)
        """
        self._action_handlers[action_type] = {
            'handler': handler_fn,
            'guard': guard_fn,
        }
        logger.debug(f"Registered action handler: {action_type}")

    def register_progress_callbacks(self, init_fn, finish_fn, update_fn=None, history_fn=None):
        """Register callbacks for live progress tracking from web_server.py."""
        self._progress_init_fn = init_fn
        self._progress_finish_fn = finish_fn
        self._progress_update_fn = update_fn
        self._history_record_fn = history_fn

    @staticmethod
    def _sanitize_signal_name(name):
        """Sanitize signal name: lowercase, alphanumeric + underscore/hyphen, max 50 chars."""
        if not name:
            return ''
        name = name.lower().strip()
        name = re.sub(r'[^a-z0-9_\-]', '_', name)
        name = re.sub(r'_+', '_', name).strip('_')
        return name[:50]

    # --- System Automations ---

    def ensure_system_automations(self):
        """Create system automations if they don't exist, and schedule next_run respecting last_run."""
        for spec in SYSTEM_AUTOMATIONS:
            existing = self.db.get_system_automation_by_action(spec['action_type'])
            if not existing:
                aid = self.db.create_automation(
                    name=spec['name'],
                    trigger_type=spec['trigger_type'],
                    trigger_config=json.dumps(spec['trigger_config']),
                    action_type=spec['action_type'],
                    action_config='{}',
                    profile_id=1,
                )
                if aid:
                    self.db.update_automation(aid, is_system=1)
                    logger.info(f"Created system automation: {spec['name']} (id={aid})")
                existing = self.db.get_system_automation_by_action(spec['action_type'])

            if existing:
                if spec.get('initial_delay') is not None:
                    # Compute full interval from trigger config
                    full_interval = self._calc_delay_seconds(spec['trigger_config'])
                    initial_delay = spec['initial_delay']

                    # Only respect last_run for longer intervals (>= 1 hour).
                    # Short-interval automations (wishlist 30min, clean downloads 5min) are cheap
                    # and users may expect them to run shortly after startup.
                    min_interval_for_skip = 3600  # 1 hour

                    # Check if last_run exists and is recent enough to skip the startup scan
                    last_run_str = existing.get('last_run')
                    last_error = existing.get('last_error')
                    if full_interval >= min_interval_for_skip and last_run_str and not last_error:
                        try:
                            last_run = datetime.strptime(last_run_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                            elapsed = (_utcnow() - last_run).total_seconds()

                            if elapsed >= 0 and elapsed < full_interval:
                                # Last run was recent and successful — schedule for when the interval naturally expires
                                remaining = full_interval - elapsed
                                nr = _utc_after(remaining)
                                self.db.update_automation(existing['id'], next_run=nr)
                                logger.info(f"System automation '{spec['name']}' last ran {elapsed/3600:.1f}h ago (ok), next run in {remaining/3600:.1f}h")
                                continue
                            else:
                                # Overdue or clock skew — run after initial delay
                                logger.info(f"System automation '{spec['name']}' last ran {elapsed/3600:.1f}h ago (overdue), running in {initial_delay}s")
                        except (ValueError, TypeError):
                            pass  # Malformed timestamp — fall through to initial delay
                    elif last_run_str and last_error:
                        logger.info(f"System automation '{spec['name']}' last run had error, retrying in {initial_delay}s")

                    # No last_run or overdue — use initial delay
                    nr = _utc_after(initial_delay)
                    self.db.update_automation(existing['id'], next_run=nr)
                    logger.info(f"System automation '{spec['name']}' next_run set to {initial_delay}s from now")
                else:
                    logger.info(f"System automation '{spec['name']}' ready (event-based)")

    def get_system_automation_next_run_seconds(self, action_type):
        """Get seconds until next run for a system automation. Returns 0 if not found or disabled."""
        auto = self.db.get_system_automation_by_action(action_type)
        if not auto or not auto.get('enabled') or not auto.get('next_run'):
            return 0
        try:
            next_run = datetime.strptime(auto['next_run'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            remaining = (next_run - _utcnow()).total_seconds()
            return max(0, int(remaining))
        except (ValueError, TypeError):
            return 0

    # --- Lifecycle ---

    def start(self):
        """Load all enabled automations from DB and schedule them."""
        self._running = True
        self._event_cache_dirty = True
        self.ensure_system_automations()
        automations = self.db.get_automations()
        scheduled = 0
        event_count = 0
        for auto in automations:
            if auto.get('enabled'):
                trigger_type = auto.get('trigger_type', '')
                if trigger_type in self._trigger_handlers:
                    self.schedule_automation(auto['id'])
                    scheduled += 1
                else:
                    event_count += 1
        # Pre-build event cache
        self._rebuild_event_cache()
        logger.info(f"AutomationEngine started — {scheduled} scheduled, {event_count} event-based")

    def stop(self):
        """Cancel all timers on shutdown."""
        self._running = False
        with self._lock:
            for _aid, timer in self._timers.items():
                timer.cancel()
            count = len(self._timers)
            self._timers.clear()
        if count:
            logger.info(f"AutomationEngine stopped — cancelled {count} timer(s)")

    # --- Scheduling ---

    def schedule_automation(self, automation_id):
        """Set up timer for a single automation based on its trigger type."""
        auto = self.db.get_automation(automation_id)
        if not auto or not auto.get('enabled'):
            return

        trigger_type = auto.get('trigger_type')
        setup_fn = self._trigger_handlers.get(trigger_type)

        if not setup_fn:
            # Event-based trigger — no timer needed, just invalidate cache
            self._event_cache_dirty = True
            return

        try:
            config = json.loads(auto.get('trigger_config') or '{}')
        except json.JSONDecodeError:
            config = {}

        self.cancel_automation(automation_id)
        setup_fn(automation_id, config)

    def cancel_automation(self, automation_id):
        """Cancel timer for an automation and invalidate event cache."""
        with self._lock:
            timer = self._timers.pop(automation_id, None)
            if timer:
                timer.cancel()
        self._event_cache_dirty = True

    # --- Event Bus ---

    def emit(self, event_type, data):
        """Called from web_server.py when events occur. Non-blocking."""
        if not self._running:
            return
        thread = threading.Thread(
            target=self._process_event,
            args=(event_type, dict(data)),
            daemon=True,
            name=f'automation-event-{event_type}'
        )
        thread.start()

    def _process_event(self, event_type, data):
        """Find matching automations and run them."""
        try:
            # Signal safety: chain depth limit and cooldown
            if event_type.startswith('signal:'):
                depth = data.get('_chain_depth', 0)
                if depth >= self._max_chain_depth:
                    logger.warning(f"Signal chain depth limit ({self._max_chain_depth}) reached for {event_type}, stopping")
                    return
                with self._lock:
                    now = time.time()
                    last = self._signal_cooldowns.get(event_type, 0)
                    if now - last < self._signal_cooldown_seconds:
                        logger.info(f"Signal {event_type} on cooldown ({self._signal_cooldown_seconds}s), skipping")
                        return
                    self._signal_cooldowns[event_type] = now

            if self._event_cache_dirty:
                self._rebuild_event_cache()

            automation_ids = self._event_automations.get(event_type, [])
            if not automation_ids:
                logger.info(f"Event '{event_type}' — no automations registered in cache. Cache keys: {list(self._event_automations.keys())}")
                return

            logger.info(f"Event '{event_type}' — checking {len(automation_ids)} automation(s), data={data}")
            for aid in automation_ids:
                try:
                    auto = self.db.get_automation(aid)
                    if not auto or not auto.get('enabled'):
                        logger.debug(f"Event '{event_type}' — automation {aid} disabled or not found, skipping")
                        continue
                    config = json.loads(auto.get('trigger_config') or '{}')
                    if self._evaluate_conditions(config, data):
                        logger.info(f"Event '{event_type}' MATCHED automation '{auto.get('name')}' (id={aid})")
                        # Run in separate thread so delays don't block the event loop
                        threading.Thread(
                            target=self._run_event_automation,
                            args=(auto, aid, data),
                            daemon=True,
                            name=f'automation-exec-{aid}'
                        ).start()
                    else:
                        logger.info(f"Event '{event_type}' conditions NOT MET for automation '{auto.get('name')}' (id={aid}), config={config}, data={data}")
                except Exception as e:
                    logger.error(f"Event automation {aid} error: {e}")
        except Exception as e:
            logger.error(f"Event processing error for '{event_type}': {e}")

    def _rebuild_event_cache(self):
        """Cache which automations listen to which event types."""
        new_cache = {}
        try:
            all_autos = self.db.get_automations()
            for auto in all_autos:
                if not auto.get('enabled'):
                    continue
                tt = auto.get('trigger_type', '')
                if tt == 'signal_received':
                    # Signal triggers map to 'signal:{name}' event key
                    try:
                        tc = json.loads(auto.get('trigger_config') or '{}')
                    except (json.JSONDecodeError, TypeError):
                        tc = {}
                    sig = tc.get('signal_name', '')
                    if sig:
                        key = 'signal:' + self._sanitize_signal_name(sig)
                        new_cache.setdefault(key, []).append(auto['id'])
                elif tt and tt not in self._trigger_handlers:
                    new_cache.setdefault(tt, []).append(auto['id'])
        except Exception as e:
            logger.error(f"Failed to rebuild event cache: {e}")
        # Atomic swap — safe for concurrent readers
        self._event_automations = new_cache
        self._event_cache_dirty = False
        logger.debug(f"Event cache rebuilt: {dict((k, len(v)) for k, v in self._event_automations.items())}")

    def _evaluate_conditions(self, trigger_config, event_data):
        """Check if event data matches trigger conditions. No conditions = always match."""
        conditions = trigger_config.get('conditions', [])
        if not conditions:
            return True

        match_mode = trigger_config.get('match', 'all')
        results = []

        for cond in conditions:
            field = cond.get('field', '')
            operator = cond.get('operator', 'contains')
            value = cond.get('value', '').lower()
            event_value = str(event_data.get(field, '')).lower()

            if operator == 'contains':
                results.append(value in event_value)
            elif operator == 'equals':
                results.append(value == event_value)
            elif operator == 'starts_with':
                results.append(event_value.startswith(value))
            elif operator == 'not_contains':
                results.append(value not in event_value)
            else:
                results.append(False)

        if match_mode == 'any':
            return any(results)
        return all(results)

    def _run_event_automation(self, auto, automation_id, event_data):
        """Execute action for an event-triggered automation."""
        action_type = auto.get('action_type')

        # Check for action delay
        try:
            action_config = json.loads(auto.get('action_config') or '{}')
        except json.JSONDecodeError:
            action_config = {}

        # Inject automation identity for progress tracking
        action_config['_automation_id'] = automation_id
        action_config['_automation_name'] = auto.get('name', '')
        # Merge event data so action handlers can access trigger context
        if event_data:
            action_config['_event_data'] = event_data
            # Forward playlist_id from event if action config doesn't have one set
            if not action_config.get('playlist_id') and event_data.get('playlist_id'):
                action_config['playlist_id'] = event_data['playlist_id']

        delay_minutes = action_config.get('delay', 0)
        _delay_already_inited = False
        if delay_minutes and delay_minutes > 0:
            # Initialize progress BEFORE delay so card glows during wait
            if self._progress_init_fn:
                try:
                    self._progress_init_fn(automation_id, auto.get('name', ''), action_type)
                except Exception as e:
                    logger.debug("event progress init (delay): %s", e)
                _delay_already_inited = True

            delay_seconds = int(delay_minutes) * 60
            logger.info(f"Event automation '{auto.get('name')}' delaying {delay_minutes}m before action")
            for remaining in range(delay_seconds, 0, -1):
                if not self._running:
                    return
                if self._progress_update_fn and remaining % 5 == 0:
                    mins, secs = divmod(remaining, 60)
                    self._progress_update_fn(automation_id,
                        phase=f'Delay: {mins}m {secs}s remaining',
                        progress=int((delay_seconds - remaining) / delay_seconds * 10))
                time.sleep(1)

        # notify_only = no action, just send notification with event data
        if action_type == 'notify_only':
            result = {'status': 'triggered'}
        else:
            handler_info = self._action_handlers.get(action_type)
            if not handler_info:
                result = {'status': 'error', 'error': f'No handler for {action_type}'}
                logger.warning(f"No handler for action '{action_type}' on event automation {automation_id}")
            else:
                guard_fn = handler_info.get('guard')
                if guard_fn and guard_fn():
                    result = {'status': 'skipped', 'reason': f'{action_type} already running'}
                    logger.info(f"Event automation '{auto.get('name')}' skipped — {action_type} busy")
                    # If progress was initialized during delay, finalize it
                    if _delay_already_inited and self._progress_finish_fn:
                        try:
                            self._progress_finish_fn(automation_id, result)
                        except Exception as e:
                            logger.debug("event progress finish (skipped): %s", e)
                else:
                    # Initialize progress tracking (skip if already done during delay)
                    if not _delay_already_inited and self._progress_init_fn:
                        try:
                            self._progress_init_fn(automation_id, auto.get('name', ''), action_type)
                        except Exception as e:
                            logger.debug("event progress init: %s", e)
                    try:
                        result = handler_info['handler'](action_config) or {}
                        logger.info(f"Event automation '{auto.get('name')}' executed: {result.get('status', 'ok')}")
                    except Exception as e:
                        result = {'status': 'error', 'error': str(e)}
                        logger.error(f"Event automation '{auto.get('name')}' action failed: {e}")
                    # Finalize progress tracking
                    if self._progress_finish_fn:
                        try:
                            self._progress_finish_fn(automation_id, result)
                        except Exception as e:
                            logger.debug("event progress finish: %s", e)

        # Merge event data into result for then-action variables
        merged = {**event_data, **result}
        chain_depth = event_data.get('_chain_depth', 0)

        try:
            self._execute_then_actions(auto, merged, chain_depth)
        except Exception as e:
            logger.error(f"Then-actions failed for event automation {automation_id}: {e}")

        # Update run stats (no reschedule — event triggers don't use timers)
        last_result = json.dumps({k: v for k, v in merged.items() if not k.startswith('_')})
        # Surface every failure mode to last_error: handlers in this codebase use
        # 'error', 'reason', or 'message' interchangeably when returning gracefully.
        if result.get('status') == 'error':
            error = (
                result.get('error')
                or result.get('reason')
                or result.get('message')
                or 'Handler reported failure'
            )
        else:
            error = None
        self.db.update_automation_run(automation_id, error=error, last_result=last_result)

        if self._history_record_fn:
            try:
                self._history_record_fn(automation_id, result)
            except Exception as e:
                logger.debug("history record failed: %s", e)

    # --- Schedule Execution (timer-based) ---

    def run_automation(self, automation_id, skip_delay=False, profile_id=None):
        """Execute: check guard → run action → send notification → update stats → reschedule."""
        if not self._running:
            return

        auto = self.db.get_automation(automation_id)
        if not auto or not auto.get('enabled'):
            return

        action_type = auto.get('action_type')

        # notify_only for scheduled automations
        if action_type == 'notify_only':
            result = {'status': 'triggered'}
            try:
                self._execute_then_actions(auto, result, chain_depth=0)
            except Exception as e:
                logger.error(f"Then-actions failed for automation {automation_id}: {e}")
            self._finish_run(auto, automation_id, result, error=None)
            return

        handler_info = self._action_handlers.get(action_type)
        if not handler_info:
            logger.warning(f"No handler for action '{action_type}' on automation {automation_id}")
            self.db.update_automation_run(automation_id, error=f"No handler for action: {action_type}")
            return

        try:
            action_config = json.loads(auto.get('action_config') or '{}')
        except json.JSONDecodeError:
            action_config = {}

        # Inject automation identity for progress tracking
        action_config['_automation_id'] = automation_id
        action_config['_automation_name'] = auto.get('name', '')
        if profile_id is not None:
            action_config['_profile_id'] = profile_id

        # Action delay (skipped for manual run_now)
        delay_minutes = action_config.get('delay', 0)
        _delay_already_inited = False
        if not skip_delay and delay_minutes and delay_minutes > 0:
            # Initialize progress BEFORE delay so card glows during wait
            if self._progress_init_fn:
                try:
                    self._progress_init_fn(automation_id, auto.get('name', ''), action_type)
                except Exception as e:
                    logger.debug("scheduled progress init (delay): %s", e)
                _delay_already_inited = True

            delay_seconds = int(delay_minutes) * 60
            logger.info(f"Automation '{auto['name']}' delaying {delay_minutes}m before action")
            for remaining in range(delay_seconds, 0, -1):
                if not self._running:
                    return
                if self._progress_update_fn and remaining % 5 == 0:
                    mins, secs = divmod(remaining, 60)
                    self._progress_update_fn(automation_id,
                        phase=f'Delay: {mins}m {secs}s remaining',
                        progress=int((delay_seconds - remaining) / delay_seconds * 10))
                time.sleep(1)

        # Check guard (is the operation already running?)
        guard_fn = handler_info.get('guard')
        if guard_fn and guard_fn():
            result = {'status': 'skipped', 'reason': f'{action_type} is already running'}
            logger.info(f"Automation '{auto['name']}' skipped — {action_type} already running")
            # If progress was initialized during delay, finalize it
            if _delay_already_inited and self._progress_finish_fn:
                try:
                    self._progress_finish_fn(automation_id, result)
                except Exception as e:
                    logger.debug("scheduled progress finish (skipped): %s", e)
            self._finish_run(auto, automation_id, result, error=None)
            return

        # Initialize progress tracking (skip if already done during delay)
        if not _delay_already_inited and self._progress_init_fn:
            try:
                self._progress_init_fn(automation_id, auto.get('name', ''), action_type)
            except Exception as e:
                logger.debug("scheduled progress init: %s", e)

        # Execute the action
        error = None
        result = {}
        try:
            result = handler_info['handler'](action_config) or {}
            logger.info(f"Automation '{auto['name']}' (id={automation_id}) executed: {result.get('status', 'ok')}")
            # Handlers may signal failure by RETURNING {'status': 'error', ...} instead of
            # raising. Surface that to the DB so `last_error` reflects every failure mode,
            # not just uncaught exceptions. Falls back through ('error', 'reason', 'message')
            # because handlers in this codebase aren't consistent about which key they set.
            if result.get('status') == 'error':
                error = (
                    result.get('error')
                    or result.get('reason')
                    or result.get('message')
                    or 'Handler reported failure'
                )
        except Exception as e:
            error = str(e)
            result = {'status': 'error', 'error': error}
            logger.error(f"Automation '{auto['name']}' (id={automation_id}) failed: {e}")

        # Finalize progress tracking
        if self._progress_finish_fn:
            try:
                self._progress_finish_fn(automation_id, result)
            except Exception as e:
                logger.debug("scheduled progress finish: %s", e)

        # Execute then-actions (notifications + fire_signal)
        try:
            self._execute_then_actions(auto, result, chain_depth=0)
        except Exception as e:
            logger.error(f"Then-actions failed for automation {automation_id}: {e}")

        self._finish_run(auto, automation_id, result, error)

    def _finish_run(self, auto, automation_id, result, error):
        """Update DB with run stats and reschedule."""
        next_run_str = None
        trigger_type = auto.get('trigger_type', '')
        # Only compute next_run for timer-based triggers (event triggers don't have scheduled runs)
        if trigger_type in self._trigger_handlers:
            try:
                trigger_config = json.loads(auto.get('trigger_config') or '{}')
                if trigger_type == 'daily_time':
                    # Next run is tomorrow at the configured time (compute delay from local time, store as UTC)
                    time_str = trigger_config.get('time', '00:00')
                    hour, minute = map(int, time_str.split(':'))
                    now_local = datetime.now()
                    target = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=1)
                    next_run_str = _utc_after((target - now_local).total_seconds())
                elif trigger_type == 'weekly_time':
                    time_str = trigger_config.get('time', '00:00')
                    hour, minute = map(int, time_str.split(':'))
                    now_local = datetime.now()
                    target = self._next_weekly_occurrence(hour, minute, trigger_config.get('days', []))
                    next_run_str = _utc_after((target - now_local).total_seconds())
                else:
                    delay = self._calc_delay_seconds(trigger_config)
                    if delay:
                        next_run_str = _utc_after(delay)
            except Exception as e:
                logger.debug("next run calc failed: %s", e)

        last_result = json.dumps(result) if result else None
        self.db.update_automation_run(automation_id, next_run=next_run_str, error=error, last_result=last_result)

        if self._history_record_fn:
            try:
                self._history_record_fn(automation_id, result)
            except Exception as e:
                logger.debug("history record failed: %s", e)

        if self._running:
            self.schedule_automation(automation_id)

    def run_now(self, automation_id, profile_id=None):
        """Manual trigger — run immediately in a background thread.
        Always uses run_automation (skips condition checks and action delay).

        Args:
            automation_id: ID of automation to run
            profile_id: If provided, scopes the run to this profile (manual trigger from UI)
        """
        auto = self.db.get_automation(automation_id)
        if not auto:
            return False

        thread = threading.Thread(
            target=self.run_automation,
            args=(automation_id, True, profile_id),
            daemon=True,
            name=f'automation-run-{automation_id}'
        )
        thread.start()
        return True

    # --- Trigger handlers ---

    def _calc_delay_seconds(self, config):
        """Calculate delay in seconds from schedule config."""
        interval = config.get('interval', 1)
        unit = config.get('unit', 'hours')
        multipliers = {'minutes': 60, 'hours': 3600, 'days': 86400}
        return max(int(interval), 1) * multipliers.get(unit, 3600)

    def _setup_schedule_trigger(self, automation_id, config):
        """Config: {"interval": 6, "unit": "hours"}"""
        delay = self._calc_delay_seconds(config)

        # If there's a next_run in the future, use remaining time instead
        auto = self.db.get_automation(automation_id)
        if auto and auto.get('next_run'):
            try:
                next_run = datetime.strptime(auto['next_run'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                remaining = (next_run - _utcnow()).total_seconds()
                if remaining > 0:
                    delay = remaining
            except (ValueError, TypeError):
                pass

        next_run_str = _utc_after(delay)
        self.db.update_automation(automation_id, next_run=next_run_str)

        timer = threading.Timer(delay, self.run_automation, args=(automation_id,))
        timer.daemon = True
        timer.start()

        with self._lock:
            self._timers[automation_id] = timer

        logger.debug(f"Scheduled automation {automation_id} in {delay:.0f}s")

    def _setup_daily_time_trigger(self, automation_id, config):
        """Config: {"time": "03:00"}  — runs daily at the specified local time."""
        time_str = config.get('time', '00:00')
        try:
            hour, minute = map(int, time_str.split(':'))
        except (ValueError, AttributeError):
            hour, minute = 0, 0

        now_local = datetime.now()
        target = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now_local:
            target += timedelta(days=1)

        delay = (target - now_local).total_seconds()

        next_run_str = _utc_after(delay)
        self.db.update_automation(automation_id, next_run=next_run_str)

        timer = threading.Timer(delay, self.run_automation, args=(automation_id,))
        timer.daemon = True
        timer.start()

        with self._lock:
            self._timers[automation_id] = timer

        logger.debug(f"Daily automation {automation_id} scheduled for {time_str} (in {delay:.0f}s)")

    def _setup_weekly_time_trigger(self, automation_id, config):
        """Config: {"time": "03:00", "days": ["mon", "wed", "fri"]}"""
        time_str = config.get('time', '00:00')
        try:
            hour, minute = map(int, time_str.split(':'))
        except (ValueError, AttributeError):
            hour, minute = 0, 0

        target = self._next_weekly_occurrence(hour, minute, config.get('days', []))
        delay = (target - datetime.now()).total_seconds()

        next_run_str = _utc_after(delay)
        self.db.update_automation(automation_id, next_run=next_run_str)

        timer = threading.Timer(delay, self.run_automation, args=(automation_id,))
        timer.daemon = True
        timer.start()

        with self._lock:
            self._timers[automation_id] = timer

        day_names = ', '.join(config.get('days', [])) or 'every day'
        logger.debug(f"Weekly automation {automation_id} scheduled for {time_str} on {day_names} (in {delay:.0f}s)")

    def _next_weekly_occurrence(self, hour, minute, days):
        """Find the next datetime matching one of the given weekday abbreviations."""
        day_map = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}
        allowed = {day_map[d] for d in days if d in day_map}
        if not allowed:
            allowed = set(range(7))  # no days selected = every day

        now = datetime.now()
        for offset in range(8):  # check today + next 7 days
            candidate = now + timedelta(days=offset)
            if candidate.weekday() in allowed:
                target = candidate.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if target > now:
                    return target
        # Fallback: tomorrow (shouldn't happen with 8-day scan)
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=1)

    # --- Then Actions (notifications + signals) ---

    def _execute_then_actions(self, automation, action_result, chain_depth=0):
        """Execute all THEN actions: notifications (Discord/Pushbullet/Telegram) and fire_signal."""
        # Read then_actions array
        try:
            then_actions = json.loads(automation.get('then_actions') or '[]')
        except (json.JSONDecodeError, TypeError):
            then_actions = []

        # Backward compat: fall back to notify_type/notify_config
        if not then_actions:
            nt = automation.get('notify_type')
            if nt:
                try:
                    nc = json.loads(automation.get('notify_config') or '{}')
                except (json.JSONDecodeError, TypeError):
                    nc = {}
                then_actions = [{'type': nt, 'config': nc}]

        if not then_actions:
            return

        # Build template variables
        variables = {
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'name': automation.get('name', 'Automation'),
            'run_count': str(automation.get('run_count', 0) + 1),
            'status': action_result.get('status', 'unknown'),
        }
        for k, v in action_result.items():
            if not k.startswith('_'):
                variables[k] = str(v)

        for item in then_actions:
            try:
                t = item.get('type', '')
                c = item.get('config', {})
                if t == 'discord_webhook':
                    self._send_discord_notification(c, variables)
                elif t == 'pushbullet':
                    self._send_pushbullet_notification(c, variables)
                elif t == 'telegram':
                    self._send_telegram_notification(c, variables)
                elif t == 'webhook':
                    self._send_webhook(c, variables)
                elif t == 'fire_signal':
                    sig = self._sanitize_signal_name(c.get('signal_name', ''))
                    if sig:
                        emit_data = {k: v for k, v in action_result.items() if not k.startswith('_')}
                        emit_data['_chain_depth'] = chain_depth + 1
                        emit_data['signal_name'] = sig
                        logger.info(f"Automation '{automation.get('name')}' firing signal: {sig} (depth={chain_depth + 1})")
                        self.emit('signal:' + sig, emit_data)
                elif t == 'run_script':
                    handler = self._action_handlers.get('run_script')
                    if handler:
                        script_config = dict(c)
                        # Pass action result as environment context
                        script_config['_automation_name'] = automation.get('name', '')
                        script_config['_event_data'] = {'type': 'then_action', 'result': {k: str(v) for k, v in action_result.items() if not k.startswith('_')}}
                        handler['handler'](script_config)
            except Exception as e:
                logger.error(f"Then-action '{item.get('type')}' failed for automation {automation.get('id')}: {e}")

    # --- Signal Cycle Detection ---

    def detect_signal_cycles(self, automations_list):
        """Build signal dependency graph from automations list, return cycle path or None.
        Used by web_server.py to validate before saving an automation."""
        # Build graph: signal listened → set of signals fired
        graph = {}
        for auto in automations_list:
            if not auto.get('enabled', True):
                continue
            tt = auto.get('trigger_type', '')
            if tt != 'signal_received':
                continue
            tc = auto.get('trigger_config') or {}
            if isinstance(tc, str):
                try:
                    tc = json.loads(tc)
                except (json.JSONDecodeError, TypeError):
                    tc = {}
            listen_sig = self._sanitize_signal_name(tc.get('signal_name', ''))
            if not listen_sig:
                continue
            # What signals does this automation fire?
            ta = auto.get('then_actions') or '[]'
            if isinstance(ta, str):
                try:
                    ta = json.loads(ta)
                except (json.JSONDecodeError, TypeError):
                    ta = []
            for item in ta:
                if item.get('type') == 'fire_signal':
                    fire_sig = self._sanitize_signal_name(item.get('config', {}).get('signal_name', ''))
                    if fire_sig:
                        graph.setdefault(listen_sig, set()).add(fire_sig)

        # DFS cycle detection with ordered path for readable error messages
        def has_cycle(node, visited, path_list, path_set):
            if node in path_set:
                # Extract the cycle portion from path_list
                cycle_start = path_list.index(node)
                return path_list[cycle_start:] + [node]
            if node in visited:
                return None
            visited.add(node)
            path_list.append(node)
            path_set.add(node)
            for neighbor in graph.get(node, []):
                result = has_cycle(neighbor, visited, path_list, path_set)
                if result:
                    return result
            path_list.pop()
            path_set.discard(node)
            return None

        visited = set()
        for start in graph:
            cycle = has_cycle(start, visited, [], set())
            if cycle:
                return cycle
        return None

    def _send_discord_notification(self, config, variables):
        """POST to Discord webhook with template variable substitution."""
        url = config.get('webhook_url', '').strip()
        if not url:
            raise ValueError("No webhook URL configured")

        message = config.get('message', '{name} completed with status: {status}')

        # Substitute all variables
        for key, value in variables.items():
            message = message.replace('{' + key + '}', value)

        resp = requests.post(url, json={"content": message}, timeout=10)
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"Discord webhook returned {resp.status_code}: {resp.text[:200]}")

    def _send_pushbullet_notification(self, config, variables):
        """Send push notification via Pushbullet API."""
        token = config.get('access_token', '').strip()
        if not token:
            raise ValueError("No Pushbullet access token configured")

        title = config.get('title', '{name}')
        message = config.get('message', 'Completed with status: {status}')

        for key, value in variables.items():
            title = title.replace('{' + key + '}', value)
            message = message.replace('{' + key + '}', value)

        resp = requests.post(
            'https://api.pushbullet.com/v2/pushes',
            json={"type": "note", "title": title, "body": message},
            headers={"Access-Token": token},
            timeout=10,
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Pushbullet returned {resp.status_code}: {resp.text[:200]}")

    def _send_telegram_notification(self, config, variables):
        """Send message via Telegram Bot API."""
        bot_token = config.get('bot_token', '').strip()
        chat_id = config.get('chat_id', '').strip()
        if not bot_token or not chat_id:
            raise ValueError("Bot token and chat ID are required for Telegram")

        message = config.get('message', '{name} completed with status: {status}')

        for key, value in variables.items():
            message = message.replace('{' + key + '}', value)

        resp = requests.post(
            f'https://api.telegram.org/bot{bot_token}/sendMessage',
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        data = resp.json() if resp.status_code == 200 else {}
        if not data.get('ok'):
            raise RuntimeError(f"Telegram returned {resp.status_code}: {resp.text[:200]}")

    def _send_webhook(self, config, variables):
        """Send a POST request to a user-configured webhook URL with JSON payload."""
        url = config.get('url', '').strip()
        if not url:
            raise ValueError("No webhook URL configured")

        # Build headers — always include Content-Type, plus optional custom headers
        headers = {'Content-Type': 'application/json'}
        custom_headers = config.get('headers', '').strip()
        if custom_headers:
            for line in custom_headers.split('\n'):
                line = line.strip()
                if ':' in line:
                    key, value = line.split(':', 1)
                    # Substitute variables in header values
                    for vk, vv in variables.items():
                        value = value.replace('{' + vk + '}', vv)
                    headers[key.strip()] = value.strip()

        # Build JSON payload with all variables
        payload = dict(variables)

        # Add custom message if configured
        message = config.get('message', '').strip()
        if message:
            for key, value in variables.items():
                message = message.replace('{' + key + '}', value)
            payload['message'] = message

        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        if resp.status_code >= 400:
            raise RuntimeError(f"Webhook returned {resp.status_code}: {resp.text[:200]}")

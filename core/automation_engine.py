"""
Automation Engine — trigger → action → notify scheduler for SoulSync.

Architecture:
- Triggers (WHEN): schedule timer, event-based (track_downloaded, batch_complete, etc.)
- Actions (DO): real SoulSync operations registered by web_server.py
- Notifications (NOTIFY): optional Discord webhook with template variables
- Conditions: optional filters on event data (artist contains, title equals, etc.)

Uses threading.Timer pattern for schedule triggers.
Event triggers react to emit() calls from web_server.py hook points.
"""

import json
import time
import threading
import requests
from datetime import datetime, timedelta
from utils.logging_config import get_logger

logger = get_logger("automation_engine")

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

        # Event trigger cache: trigger_type → [automation_id, ...]
        self._event_automations = {}
        self._event_cache_dirty = True

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

    def register_progress_callbacks(self, init_fn, finish_fn):
        """Register callbacks for live progress tracking from web_server.py."""
        self._progress_init_fn = init_fn
        self._progress_finish_fn = finish_fn

    # --- System Automations ---

    def ensure_system_automations(self):
        """Create system automations if they don't exist, and reset next_run to initial delays on every startup."""
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
                # Reset next_run to initial delay on every startup
                next_run = (datetime.now() + timedelta(seconds=spec['initial_delay'])).strftime('%Y-%m-%d %H:%M:%S')
                self.db.update_automation(existing['id'], next_run=next_run)
                logger.info(f"System automation '{spec['name']}' next_run reset to {spec['initial_delay']}s from now")

    def get_system_automation_next_run_seconds(self, action_type):
        """Get seconds until next run for a system automation. Returns 0 if not found or disabled."""
        auto = self.db.get_system_automation_by_action(action_type)
        if not auto or not auto.get('enabled') or not auto.get('next_run'):
            return 0
        try:
            next_run = datetime.strptime(auto['next_run'], '%Y-%m-%d %H:%M:%S')
            remaining = (next_run - datetime.now()).total_seconds()
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
            for aid, timer in self._timers.items():
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
            if self._event_cache_dirty:
                self._rebuild_event_cache()

            automation_ids = self._event_automations.get(event_type, [])
            if not automation_ids:
                return

            logger.debug(f"Event '{event_type}' — checking {len(automation_ids)} automation(s)")
            for aid in automation_ids:
                try:
                    auto = self.db.get_automation(aid)
                    if not auto or not auto.get('enabled'):
                        continue
                    config = json.loads(auto.get('trigger_config') or '{}')
                    if self._evaluate_conditions(config, data):
                        logger.info(f"Event '{event_type}' matched automation '{auto.get('name')}' (id={aid})")
                        # Run in separate thread so delays don't block the event loop
                        threading.Thread(
                            target=self._run_event_automation,
                            args=(auto, aid, data),
                            daemon=True,
                            name=f'automation-exec-{aid}'
                        ).start()
                    else:
                        logger.debug(f"Event '{event_type}' conditions not met for automation {aid}")
                except Exception as e:
                    logger.error(f"Event automation {aid} error: {e}")
        except Exception as e:
            logger.error(f"Event processing error for '{event_type}': {e}")

    def _rebuild_event_cache(self):
        """Cache which automations listen to which event types."""
        self._event_automations = {}
        try:
            all_autos = self.db.get_automations()
            for auto in all_autos:
                if not auto.get('enabled'):
                    continue
                tt = auto.get('trigger_type', '')
                if tt and tt not in self._trigger_handlers:
                    self._event_automations.setdefault(tt, []).append(auto['id'])
        except Exception as e:
            logger.error(f"Failed to rebuild event cache: {e}")
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

        delay_minutes = action_config.get('delay', 0)
        if delay_minutes and delay_minutes > 0:
            logger.info(f"Event automation '{auto.get('name')}' delaying {delay_minutes}m before action")
            time.sleep(int(delay_minutes) * 60)
            if not self._running:
                return

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
                else:
                    # Initialize progress tracking
                    if self._progress_init_fn:
                        try: self._progress_init_fn(automation_id, auto.get('name', ''), action_type)
                        except Exception: pass
                    try:
                        result = handler_info['handler'](action_config) or {}
                        logger.info(f"Event automation '{auto.get('name')}' executed: {result.get('status', 'ok')}")
                    except Exception as e:
                        result = {'status': 'error', 'error': str(e)}
                        logger.error(f"Event automation '{auto.get('name')}' action failed: {e}")
                    # Finalize progress tracking
                    if self._progress_finish_fn:
                        try: self._progress_finish_fn(automation_id, result)
                        except Exception: pass

        # Merge event data into result for notification variables
        merged = {**event_data, **result}

        try:
            self._send_notification(auto, merged)
        except Exception as e:
            logger.error(f"Notification failed for event automation {automation_id}: {e}")

        # Update run stats (no reschedule — event triggers don't use timers)
        last_result = json.dumps(merged)
        error = result.get('error') if result.get('status') == 'error' else None
        self.db.update_automation_run(automation_id, error=error, last_result=last_result)

    # --- Schedule Execution (timer-based) ---

    def run_automation(self, automation_id, skip_delay=False):
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
                self._send_notification(auto, result)
            except Exception as e:
                logger.error(f"Notification failed for automation {automation_id}: {e}")
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

        # Action delay (skipped for manual run_now)
        delay_minutes = action_config.get('delay', 0)
        if not skip_delay and delay_minutes and delay_minutes > 0:
            logger.info(f"Automation '{auto['name']}' delaying {delay_minutes}m before action")
            time.sleep(int(delay_minutes) * 60)
            if not self._running:
                return

        # Check guard (is the operation already running?)
        guard_fn = handler_info.get('guard')
        if guard_fn and guard_fn():
            result = {'status': 'skipped', 'reason': f'{action_type} is already running'}
            logger.info(f"Automation '{auto['name']}' skipped — {action_type} already running")
            self._finish_run(auto, automation_id, result, error=None)
            return

        # Initialize progress tracking
        if self._progress_init_fn:
            try: self._progress_init_fn(automation_id, auto.get('name', ''), action_type)
            except Exception: pass

        # Execute the action
        error = None
        result = {}
        try:
            result = handler_info['handler'](action_config) or {}
            logger.info(f"Automation '{auto['name']}' (id={automation_id}) executed: {result.get('status', 'ok')}")
        except Exception as e:
            error = str(e)
            result = {'status': 'error', 'error': error}
            logger.error(f"Automation '{auto['name']}' (id={automation_id}) failed: {e}")

        # Finalize progress tracking
        if self._progress_finish_fn:
            try: self._progress_finish_fn(automation_id, result)
            except Exception: pass

        # Send notification if configured
        try:
            self._send_notification(auto, result)
        except Exception as e:
            logger.error(f"Notification failed for automation {automation_id}: {e}")

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
                    # Next run is tomorrow at the configured time
                    time_str = trigger_config.get('time', '00:00')
                    hour, minute = map(int, time_str.split(':'))
                    target = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=1)
                    next_run_str = target.strftime('%Y-%m-%d %H:%M:%S')
                elif trigger_type == 'weekly_time':
                    time_str = trigger_config.get('time', '00:00')
                    hour, minute = map(int, time_str.split(':'))
                    target = self._next_weekly_occurrence(hour, minute, trigger_config.get('days', []))
                    next_run_str = target.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    delay = self._calc_delay_seconds(trigger_config)
                    if delay:
                        next_run_str = (datetime.now() + timedelta(seconds=delay)).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                pass

        last_result = json.dumps(result) if result else None
        self.db.update_automation_run(automation_id, next_run=next_run_str, error=error, last_result=last_result)

        if self._running:
            self.schedule_automation(automation_id)

    def run_now(self, automation_id):
        """Manual trigger — run immediately in a background thread.
        Always uses run_automation (skips condition checks and action delay)."""
        auto = self.db.get_automation(automation_id)
        if not auto:
            return False

        thread = threading.Thread(
            target=self.run_automation,
            args=(automation_id, True),
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
                next_run = datetime.strptime(auto['next_run'], '%Y-%m-%d %H:%M:%S')
                remaining = (next_run - datetime.now()).total_seconds()
                if remaining > 0:
                    delay = remaining
            except (ValueError, TypeError):
                pass

        next_run_str = (datetime.now() + timedelta(seconds=delay)).strftime('%Y-%m-%d %H:%M:%S')
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

        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)

        delay = (target - now).total_seconds()

        next_run_str = target.strftime('%Y-%m-%d %H:%M:%S')
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

        next_run_str = target.strftime('%Y-%m-%d %H:%M:%S')
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

    # --- Notification ---

    def _send_notification(self, automation, action_result):
        """Send notification after action completes, if configured."""
        notify_type = automation.get('notify_type')
        if not notify_type:
            return

        try:
            config = json.loads(automation.get('notify_config') or '{}')
        except json.JSONDecodeError:
            config = {}

        # Build template variables
        variables = {
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'name': automation.get('name', 'Automation'),
            'run_count': str(automation.get('run_count', 0) + 1),
            'status': action_result.get('status', 'unknown'),
        }
        # Add all action result fields as variables
        for k, v in action_result.items():
            variables[k] = str(v)

        if notify_type == 'discord_webhook':
            self._send_discord_notification(config, variables)
        elif notify_type == 'pushbullet':
            self._send_pushbullet_notification(config, variables)
        elif notify_type == 'telegram':
            self._send_telegram_notification(config, variables)

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

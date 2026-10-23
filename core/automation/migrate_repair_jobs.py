"""Seed system automations for repair jobs (#1289 item 12).

Each maintenance job gets one system-owned automation row (schedule trigger +
``run_repair_job`` action) so the automation engine drives job scheduling and
the RepairWorker's staleness queue can retire.

Idempotent: safe to run on every boot. Existing rows are never clobbered —
a user who customized a job's trigger keeps their version.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("soulsync.automation.migrate_repair_jobs")

# Marker so we can find our rows later without parsing names.
_SYSTEM_OWNER = "system:repair_job"


def _find_system_automation(database, profile_id: int, job_id: str,
                            action_type: str = "run_repair_job"):
    """Return the automation row for this job, if we created one.

    Returns the first match; callers should dedup if multiples exist.
    """
    try:
        automations = database.get_automations(profile_id)
    except Exception:
        return None
    matches = []
    for a in automations or []:
        try:
            if a.get("owned_by") != _SYSTEM_OWNER:
                continue
            if a.get("action_type") != action_type:
                continue
            cfg = a.get("action_config") or "{}"
            if isinstance(cfg, str):
                cfg = json.loads(cfg)
            if cfg.get("job_id") == job_id:
                matches.append(a)
        except Exception:
            # Malformed JSON or missing keys: treat as non-match so we
            # don't seed a duplicate. Log it.
            logger.debug("Skipping malformed system automation row for %s", job_id)
            continue
    if len(matches) > 1:
        logger.warning("Found %d system automations for job %s; using first",
                       len(matches), job_id)
    return matches[0] if matches else None


def set_system_job_enabled(database, engine, job_id: str, enabled: bool,
                           action_type: str = "run_repair_job") -> bool:
    """Mirror a Tools toggle onto the job's system automation and (re)arm or
    cancel its timer now. Flipping only the row left an enabled job unscheduled
    until the next restart, and a timer that fired while it was off never
    re-armed. Returns whether a row was found."""
    auto = _find_system_automation(database, 1, job_id, action_type)
    if not auto:
        return False
    database.update_automation(auto["id"], enabled=1 if enabled else 0)
    if engine is not None:
        (engine.schedule_automation if enabled else engine.cancel_automation)(auto["id"])
    return True


# action_type -> fn(job_id, enabled). the reverse of set_system_job_enabled:
# the Automations page flips the row, these write the job's own toggle so
# Tools shows (and run_job_now respects) the same state. registered by
# web_server since the music worker lives there.
_job_toggle_sinks: dict = {}


def register_job_toggle_sink(action_type: str, fn) -> None:
    _job_toggle_sinks[action_type] = fn


def _job_id_of(auto) -> str | None:
    if not auto or auto.get("owned_by") != _SYSTEM_OWNER:
        return None
    try:
        cfg = auto.get("action_config") or "{}"
        if isinstance(cfg, str):
            cfg = json.loads(cfg)
        return cfg.get("job_id")
    except Exception:
        return None


def mirror_automation_toggle(database, automation_id: int) -> bool:
    """Push a system automation's enabled flag onto its job's Tools toggle.
    toggling from the Automations page only wrote the row, so Tools kept the
    old state and the music handler skipped the run as 'switched off in Tools'.
    returns whether a job was updated."""
    try:
        auto = database.get_automation(automation_id)
    except Exception:
        return False
    job_id = _job_id_of(auto)
    sink = _job_toggle_sinks.get(auto.get("action_type")) if job_id else None
    if not sink:
        return False
    try:
        sink(job_id, bool(auto.get("enabled")))
        return True
    except Exception as e:
        logger.debug("Could not mirror automation toggle to job %s: %s", job_id, e)
        return False


def _validate_interval(value, job_id: str) -> float | None:
    """Validate an interval_hours value. Returns float hours or None if invalid."""
    try:
        hours = float(value)
        if hours <= 0:
            return None
        # Cap at 1 year to catch garbage like string-repetition accidents.
        if hours > 8760:
            logger.warning("Job %s has absurd interval %s; skipping", job_id, value)
            return None
        return hours
    except (TypeError, ValueError):
        logger.warning("Job %s has non-numeric interval %r; skipping", job_id, value)
        return None


def _get_video_job_config(video_db, job_id: str) -> dict:
    """Read a video job's config from the video DB settings table."""
    try:
        from core.video.repair import get_all_jobs
        cls = get_all_jobs().get(job_id)
        if not cls:
            return {}
        cfg = {"enabled": cls.default_enabled,
               "interval_hours": cls.default_interval_hours}
        raw = video_db.get_setting(f"video_repair.jobs.{job_id}")
        if raw:
            saved = json.loads(raw) if isinstance(raw, str) else dict(raw)
            cfg.update(saved)
        return cfg
    except Exception as e:
        logger.debug("Could not read video job config for %s: %s", job_id, e)
        return {}


def _reconcile_job_toggle(auto, job_id, job_cls, prefix, is_video,
                          config_manager, video_db) -> None:
    """Heal a job whose Tools toggle drifted from its row. Tools always wrote
    through to the row, so drift only ever came from an Automations-page
    toggle: the row is the newer intent, and it's what actually schedules."""
    row_enabled = bool(auto.get("enabled"))
    try:
        if is_video:
            if video_db is None:
                return
            cfg = _get_video_job_config(video_db, job_id)
            if not cfg or bool(cfg.get("enabled")) == row_enabled:
                return
            raw = video_db.get_setting(f"{prefix}.{job_id}")
            saved = (json.loads(raw) if isinstance(raw, str) else dict(raw)) if raw else {}
            saved["enabled"] = row_enabled
            video_db.set_setting(f"{prefix}.{job_id}", json.dumps(saved))
        else:
            current = config_manager.get(f"{prefix}.{job_id}.enabled",
                                         getattr(job_cls, "default_enabled", False))
            if bool(current) == row_enabled:
                return
            config_manager.set(f"{prefix}.{job_id}.enabled", row_enabled)
        logger.info("Synced %s Tools toggle to its automation (%s)",
                    job_id, "on" if row_enabled else "off")
    except Exception as e:
        logger.debug("Could not reconcile toggle for %s: %s", job_id, e)


def ensure_repair_job_automations(engine, database, config_manager,
                                  profile_id: int = 1,
                                  video_db=None) -> dict:
    """Create one system automation per repair job that wants scheduling.

    Covers both music and video repair jobs. Returns {'created': N, 'skipped': M}.

    Also bridges the master toggles: sets the engine's
    ``automation_master_{music,video}_enabled`` metadata from the existing
    worker toggles so scheduled jobs honor the Tools master switches.
    """
    from core.repair_jobs import get_all_jobs as get_music_jobs
    from core.video.repair import get_all_jobs as get_video_jobs

    created, skipped = 0, 0

    # Bridge master toggles → engine metadata.
    # Music: worker's repair.master_enabled config.
    try:
        music_master = config_manager.get("repair.master_enabled", True)
        # Only set if not already set — don't clobber a user's engine-level choice.
        if database.get_metadata("automation_master_music_enabled") is None:
            database.set_metadata("automation_master_music_enabled",
                                  "1" if music_master else "0")
    except Exception as e:
        logger.debug("Could not bridge music master toggle: %s", e)

    # Video: video worker's master toggle (stored in video DB).
    if video_db is not None:
        try:
            video_master = video_db.get_setting("video_repair.master_enabled")
            # get_setting returns JSON string or None; default True (old behavior ran jobs).
            if video_master is None:
                video_master_enabled = True
            else:
                video_master_enabled = json.loads(video_master) if isinstance(video_master, str) else bool(video_master)
            if database.get_metadata("automation_master_video_enabled") is None:
                database.set_metadata("automation_master_video_enabled",
                                      "1" if video_master_enabled else "0")
        except Exception as e:
            logger.debug("Could not bridge video master toggle: %s", e)

    # (jobs dict, config prefix, action type, is_video)
    sources = [
        (get_music_jobs(), "repair.jobs", "run_repair_job", False),
        (get_video_jobs(), "video_repair.jobs", "video_run_repair_job", True),
    ]

    for jobs, prefix, action_type, is_video in sources:
        for idx, (job_id, job_cls) in enumerate(sorted(jobs.items())):
            existing = _find_system_automation(database, profile_id, job_id, action_type)
            if existing:
                _reconcile_job_toggle(existing, job_id, job_cls, prefix, is_video,
                                      config_manager, video_db)
                skipped += 1
                continue

            # Read interval/enabled from the right store.
            if is_video and video_db is not None:
                vcfg = _get_video_job_config(video_db, job_id)
                interval_raw = vcfg.get("interval_hours",
                                        getattr(job_cls, "default_interval_hours", 24))
                enabled = vcfg.get("enabled",
                                   getattr(job_cls, "default_enabled", False))
            else:
                interval_raw = config_manager.get(
                    f"{prefix}.{job_id}.interval_hours",
                    getattr(job_cls, "default_interval_hours", 24),
                )
                enabled = config_manager.get(
                    f"{prefix}.{job_id}.enabled",
                    getattr(job_cls, "default_enabled", False),
                )

            interval_hours = _validate_interval(interval_raw, job_id)
            if interval_hours is None:
                # Manual-only (0/negative) or invalid: no schedule.
                skipped += 1
                continue

            # Stagger initial runs so jobs don't fire at once on first boot.
            # Offset by source so music and video don't collide.
            source_offset = 5 if is_video else 0
            stagger_minutes = (idx * 10 + source_offset) % max(int(interval_hours * 60), 1)

            trigger_config = {
                "interval": interval_hours,
                "unit": "hours",
            }

            try:
                auto_id = database.create_automation(
                    name=f"[System] {getattr(job_cls, 'display_name', job_id)}",
                    trigger_type="schedule",
                    trigger_config=json.dumps(trigger_config),
                    action_type=action_type,
                    action_config=json.dumps({"job_id": job_id}),
                    profile_id=profile_id,
                    notify_type=None,
                    notify_config="{}",
                    then_actions="[]",
                    group_name="Maintenance",
                    owned_by=_SYSTEM_OWNER,
                    is_system=True,
                    enabled=bool(enabled),
                )
                if not auto_id:
                    logger.warning("create_automation returned None for %s", job_id)
                    skipped += 1
                    continue
                # Stagger the first run.
                first_run = datetime.now(timezone.utc) + timedelta(minutes=stagger_minutes)
                try:
                    database.update_automation(
                        auto_id,
                        next_run=first_run.strftime("%Y-%m-%d %H:%M:%S"),
                    )
                except Exception as e:
                    logger.debug("Could not set next_run for %s: %s", job_id, e)
                # Arm the timer now.
                try:
                    engine.schedule_automation(auto_id)
                except Exception as e:
                    logger.debug("schedule_automation failed for %s: %s", job_id, e)
                created += 1
                logger.info("Seeded system automation for repair job %s", job_id)
            except Exception as e:
                logger.warning("Could not seed automation for %s: %s", job_id, e)
                skipped += 1

    return {"created": created, "skipped": skipped}

"""Quality upgrades, surfaced where people look.

The Quality Upgrade Finder and the Quality Check scanner already judge every
library file against its quality profile and file a finding when one could be
better. Those findings lived only on the Tools page. This module is the read
side (which tracks could be better, counted per album and artist) and the
"upgrade bar" the candidate inspector applies in upgrade mode: a hit only
counts as an upgrade when it reaches the profile's cutoff, not merely the
fallback the everyday download filter allows.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.downloads import decisions as _decisions
from utils.logging_config import get_logger

logger = get_logger("quality.upgrades")

# The two jobs whose findings mean "this file is below its profile".
UPGRADE_JOBS = ('quality_upgrade', 'quality_upgrade_scanner')

# Profiles that ask to keep upgrading until the cutoff. until_top is the alias
# the first profile branch wrote.
UNTIL_CUTOFF_POLICIES = frozenset({'until_cutoff', 'until_top'})


def pending_upgrades_for_tracks(database, track_ids: Iterable[Any]) -> Dict[str, dict]:
    """{track_id: {finding_id, job_id, current}} for tracks with a pending finding.

    One finding per track is enough to say "could be better"; the active
    finder's (it carries a better match) wins over the flag-only scanner's.
    """
    ids = [str(t) for t in track_ids if t is not None and str(t) != '']
    if not ids:
        return {}
    out: Dict[str, dict] = {}
    conn = None
    try:
        conn = database._get_connection()
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            rows = conn.execute(
                f"""SELECT id, job_id, entity_id, details_json FROM repair_findings
                    WHERE status = 'pending' AND entity_type = 'track'
                      AND job_id IN ({','.join('?' * len(UPGRADE_JOBS))})
                      AND entity_id IN ({','.join('?' * len(chunk))})""",
                (*UPGRADE_JOBS, *chunk),
            ).fetchall()
            for row in rows:
                key = str(row['entity_id'])
                held = out.get(key)
                if held is not None and held['job_id'] == 'quality_upgrade':
                    continue
                out[key] = {
                    'finding_id': row['id'],
                    'job_id': row['job_id'],
                    'current': _current_label(row['details_json']),
                }
    except Exception as exc:  # noqa: BLE001 - a missing badge must not break a page
        logger.debug("pending upgrade lookup failed: %s", exc)
        return {}
    finally:
        if conn is not None:
            conn.close()
    return out


def _current_label(details_json) -> str:
    try:
        details = json.loads(details_json or '{}')
    except (TypeError, ValueError):
        return ''
    if not isinstance(details, dict):
        return ''
    return str(details.get('current_format') or details.get('current_quality') or '')


def annotate_enhanced_payload(database, payload: dict) -> dict:
    """Mark tracks that could be better and count them per album, in place."""
    albums = payload.get('albums') or []
    track_ids = [t.get('id') for a in albums for t in (a.get('tracks') or [])]
    pending = pending_upgrades_for_tracks(database, track_ids)
    for album in albums:
        count = 0
        for track in album.get('tracks') or []:
            hit = pending.get(str(track.get('id')))
            if hit:
                track['quality_upgrade'] = hit
                count += 1
        album['upgradable_count'] = count
    return payload


def upgrade_bar(profile_id=None) -> Tuple[list, Optional[int], str]:
    """(targets, cutoff_index, cutoff_label) for a track's quality profile.

    cutoff_index None means "any target, strictly" (the profile doesn't ask
    for a cutoff). Empty targets means the profile imposes nothing, so no hit
    can be judged an upgrade on quality grounds.
    """
    from core.quality.selection import load_profile_by_id, targets_from_profile

    profile = load_profile_by_id(profile_id)
    targets, _fallback = targets_from_profile(profile)
    if not targets:
        return [], None, ''
    if profile.get('upgrade_policy') not in UNTIL_CUTOFF_POLICIES:
        return targets, None, targets[-1].label
    try:
        idx = int(profile.get('upgrade_cutoff_index') or 0)
    except (TypeError, ValueError):
        idx = 0
    idx = max(0, min(idx, len(targets) - 1))
    return targets, idx, targets[idx].label


def apply_upgrade_bar(pairs: List[tuple], targets: list, cutoff_index: Optional[int],
                      cutoff_label: str = '') -> List[tuple]:
    """Accepted hits that don't reach the bar become ``below_cutoff`` rejections.

    Judged on what each hit claims (it isn't downloaded yet): a value it never
    stated can't disqualify it, same rule the Prowlarr lane uses. The import
    guard still checks the real file afterwards.
    """
    if not targets:
        return list(pairs)
    from core.quality.model import satisfies_a_target_on_stated_facts

    wanted = targets if cutoff_index is None else targets[:cutoff_index + 1]
    bar = (f"your upgrade cutoff ({cutoff_label})" if cutoff_index is not None
           else "any of your profile's targets")
    out = []
    for row, decision in pairs:
        if decision.accepted:
            try:
                reaches = satisfies_a_target_on_stated_facts(row.audio_quality, wanted)
            except Exception:  # noqa: BLE001 - unjudgeable claims stay as they were
                reaches = True
            if not reaches:
                decision = _decisions.reject(
                    'below_cutoff',
                    f"{_label(row)} doesn't reach {bar}",
                    decision.score,
                )
        out.append((row, decision))
    return out


def _label(row) -> str:
    try:
        return row.audio_quality.label()
    except Exception:  # noqa: BLE001
        return str(getattr(row, 'quality', '') or 'this file')


def until_cutoff_profile_ids(database) -> Optional[set]:
    """Ids of quality profiles set to keep upgrading until their cutoff.

    None when the table can't be read (callers treat that as "none").
    """
    conn = None
    try:
        conn = database._get_connection()
        rows = conn.execute(
            f"SELECT id FROM quality_profiles WHERE upgrade_policy IN "
            f"({','.join('?' * len(UNTIL_CUTOFF_POLICIES))})",
            tuple(UNTIL_CUTOFF_POLICIES),
        ).fetchall()
        return {row['id'] for row in rows}
    except Exception as exc:  # noqa: BLE001
        logger.debug("until-cutoff profile lookup failed: %s", exc)
        return None
    finally:
        if conn is not None:
            conn.close()


def until_cutoff_finding_ids(database, limit: int = 100) -> List[int]:
    """Pending Upgrade Finder findings the "Apply Quality Upgrades" automation
    may act on: only for tracks whose profile keeps upgrading until its cutoff
    (a finding with no profile belongs to the default one), and only the active
    finder's, which carry the better version to queue. Oldest first.
    """
    profiles = until_cutoff_profile_ids(database)
    if not profiles:
        return []
    default_id = None
    conn = None
    try:
        conn = database._get_connection()
        row = conn.execute(
            "SELECT id FROM quality_profiles WHERE is_default = 1 ORDER BY id LIMIT 1").fetchone()
        default_id = row['id'] if row else None
        rows = conn.execute(
            "SELECT id, details_json FROM repair_findings WHERE job_id = 'quality_upgrade' "
            "AND status = 'pending' ORDER BY id").fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("upgrade finding lookup failed: %s", exc)
        return []
    finally:
        if conn is not None:
            conn.close()
    ids: List[int] = []
    for row in rows:
        try:
            details = json.loads(row['details_json'] or '{}')
        except (TypeError, ValueError):
            continue
        if not isinstance(details, dict) or not details.get('matched_track_data'):
            continue
        profile_id = details.get('quality_profile_id')
        if profile_id is None:
            profile_id = default_id
        if profile_id in profiles:
            ids.append(row['id'])
            if len(ids) >= max(0, int(limit)):
                break
    return ids

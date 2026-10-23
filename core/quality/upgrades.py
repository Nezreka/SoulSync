"""Quality review proposals and the candidate inspector's upgrade cutoff.

Native album/track badges derive from the live Library-v2 profile evaluator,
independent of maintenance runs. Audit findings are only the review queue.
"""

from __future__ import annotations

import json
from typing import List, Optional, Tuple

from core.downloads import decisions as _decisions
from utils.logging_config import get_logger

logger = get_logger("quality.upgrades")

# Profiles that ask to keep upgrading until the cutoff. until_top is the alias
# the first profile branch wrote.
UNTIL_CUTOFF_POLICIES = frozenset({'until_cutoff', 'until_top'})


def annotate_enhanced_payload(database, payload: dict) -> dict:
    """Keep the enhanced artist adapter, using native live-profile evaluation."""
    from core.library2.quality_eval import audio_quality_from_file, effective_track_profile, quality_issue
    from core.library2.track_files import primary_file_row

    conn = database._get_connection()
    try:
        for album in payload.get('albums') or []:
            count = 0
            for track in album.get('tracks') or []:
                track.pop('quality_upgrade', None)
                try:
                    tid = int(track['id'])
                    file = primary_file_row(conn, tid, scoped=True)
                    profile = effective_track_profile(conn, tid)
                    if (not file or (file.get('file_state') or 'active') != 'active'
                            or quality_issue(file, profile) != 'below_cutoff'):
                        continue
                    measured = audio_quality_from_file(file)
                    track['quality_upgrade'] = {
                        'lib2_track_id': tid, 'quality_profile_id': profile['id'],
                        'current': measured.label(),
                    }
                    count += 1
                except (LookupError, TypeError, ValueError) as exc:
                    logger.debug('enhanced track quality unavailable: %s', exc)
            album['upgradable_count'] = count
    finally:
        conn.close()
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


def until_cutoff_finding_ids(database, limit: int = 100, *, profile_id: int = 1) -> List[int]:
    """Native, already-wanted audit proposals and preserved legacy findings.

    Native proposals are rechecked against this owner's live file and profile.
    The scheduled action never opts an unmonitored track into monitoring.
    """
    limit = max(0, int(limit))
    if not limit:
        return []
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
            "SELECT id, job_id, entity_id, file_path, details_json FROM repair_findings "
            "WHERE status='pending' AND entity_type='track' AND (job_id='quality_upgrade' "
            "OR (job_id='quality_profile_audit' AND finding_type='quality_upgrade_review')) "
            "ORDER BY id").fetchall()
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
        if not isinstance(details, dict):
            continue
        if row['job_id'] == 'quality_profile_audit':
            if not _native_finding_is_current(database, row, details, profile_id):
                continue
        elif (profile_id != 1 or not details.get('matched_track_data')
              or details.get('quality_issue') in ('format_not_in_profile', 'format_not_targeted')
              or (details.get('quality_profile_id') or default_id) not in profiles):
            continue
        ids.append(row['id'])
        if len(ids) >= limit:
            break
    return ids


def _native_finding_is_current(database, finding, details: dict, profile_id: int) -> bool:
    from core.library_scope import library_scope
    from core.library2.quality_eval import effective_track_profile, probe_profile_file, quality_issue
    from core.library2.track_files import primary_file_row
    from core.library2.wanted import track_is_wanted
    from core.library2.manual_skips import active_skip_paths

    conn = database._get_connection()
    try:
        tid = int(details.get('lib2_track_id') or 0)
        if finding['entity_id'] != f'lib2:{tid}' or details.get('quality_issue') != 'below_cutoff':
            return False
        if int(details.get('owner_profile_id') or 1) != int(profile_id):
            return False
        with library_scope(profile_id if profile_id != 1 else 'shared'):
            file = primary_file_row(conn, tid, scoped=True)
            if (not file or file['id'] != details.get('lib2_file_id')
                    or file['path'] != finding['file_path']
                    or (file.get('file_state') or 'active') != 'active'
                    or file.get('owner_profile_id') != details.get('owner_profile_id')):
                return False
            if not track_is_wanted(conn, tid, profile_id=profile_id):
                return False
            if file['path'] in active_skip_paths(conn, ('quality', 'bit_depth'), profile_id=profile_id):
                return False
            from core.repair_jobs.base import hand_tagged_path_keys, is_hand_tagged_path
            if is_hand_tagged_path(file['path'], hand_tagged_path_keys(database)):
                return False
            profile = effective_track_profile(conn, tid)
            if profile.get('upgrade_policy') not in UNTIL_CUTOFF_POLICIES:
                return False
            observed, _measured = probe_profile_file(file)
            return quality_issue(observed, profile) == 'below_cutoff'
    except (LookupError, TypeError, ValueError, RuntimeError) as exc:
        logger.debug('native upgrade finding is stale: %s', exc)
        return False
    finally:
        conn.close()

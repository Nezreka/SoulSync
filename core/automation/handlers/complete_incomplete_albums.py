"""Opt-in completion of albums identified by the repair scanner."""

from __future__ import annotations


def auto_complete_incomplete_albums(config, deps):
    """Queue only missing tracks from pinned canonical releases."""
    event = config.get('_event_data') or {}

    if (
        event.get('job_id') != 'album_completeness'
        or event.get('run_status') != 'completed'
        or event.get('errors') != 0
    ):
        return {
            'status': 'skipped',
            'reason': 'Requires a successful Album Completeness scan',
        }

    if not callable(getattr(deps, 'get_repair_findings', None)):
        return {
            'status': 'skipped',
            'reason': 'Repair findings are unavailable',
        }

    if not callable(getattr(deps, 'queue_album_completion', None)):
        return {
            'status': 'skipped',
            'reason': 'Album completion queue is unavailable',
        }

    if not callable(getattr(deps, 'fetch_canonical_release', None)):
        return {
            'status': 'skipped',
            'reason': 'Canonical release verification is unavailable',
        }

    if not callable(getattr(deps, "get_current_album_pin", None)):
        return {"status": "skipped", "reason": "Current album pin lookup is unavailable"}

    try:
        max_albums = max(1, min(10, int(config.get('max_albums', 3))))
    except (TypeError, ValueError):
        max_albums = 3

    target_album_id = str(config.get('target_album_id') or '').strip()

    result = deps.get_repair_findings(
        job_id='album_completeness',
        finding_type='incomplete_album',
        status='pending',
        page=0,
        limit=50,
    )

    if not isinstance(result, dict):
        return {'status': 'skipped', 'reason': 'Invalid repair findings'}

    # Read all pages before selecting albums, including untargeted runs.
    # Never process a partial result if pagination fails.
    if not isinstance(result.get('items'), list):
        return {'status': 'skipped', 'reason': 'Invalid findings page'}

    findings = list(result['items'])
    try:
        total = int(result.get('total'))
        if total < 0 or len(findings) > total:
            raise ValueError('Invalid findings count')
    except (TypeError, ValueError):
        return {'status': 'skipped', 'reason': 'Invalid findings count'}

    page = 1
    while len(findings) < total:
        next_page = deps.get_repair_findings(
            job_id='album_completeness',
            finding_type='incomplete_album',
            status='pending',
            page=page,
            limit=50,
        )
        if not isinstance(next_page, dict) or not isinstance(next_page.get('items'), list):
            return {'status': 'skipped', 'reason': 'Could not read all findings'}
        if not next_page['items'] or len(findings) + len(next_page['items']) > total:
            return {'status': 'skipped', 'reason': 'Findings pagination ended unexpectedly'}
        findings.extend(next_page['items'])
        page += 1

    eligible = []
    seen_releases = set()

    for finding in findings:
        if not isinstance(finding, dict):
            continue

        if (
            finding.get('finding_type') != 'incomplete_album'
            or finding.get('status') != 'pending'
        ):
            continue

        details = finding.get('details')
        if not isinstance(details, dict):
            continue

        if target_album_id and str(details.get('album_id') or '').strip() != target_album_id:
            continue

        source = str(details.get('canonical_source') or '').strip()
        album_id = str(details.get('canonical_album_id') or '').strip()
        missing = details.get('missing_tracks')

        if not source or not album_id:
            continue

        if not isinstance(missing, list) or not missing:
            continue

        release_key = (source.casefold(), album_id)
        if release_key in seen_releases:
            continue

        seen_releases.add(release_key)
        eligible.append(finding)

    # max_albums caps what gets queued, not what gets looked at. capping the
    # candidates let cooling-down or re-pinned albums at the top eat every slot
    queued = 0
    cooling_down = getattr(deps, 'album_completion_cooling_down', None)
    for finding in eligible:
        if queued >= max_albums:
            break
        details = finding['details']

        if callable(cooling_down):
            try:
                if cooling_down(
                    str(details['canonical_source']).strip(),
                    str(details['canonical_album_id']).strip(),
                ):
                    continue
            except Exception:  # noqa: S110 — the queue's claim is the real gate
                pass

        try:
            pin = deps.get_current_album_pin(details.get("album_id"))
            # Only shared-library albums are supported. Missing ownership
            # information must fail closed, never default to shared.
            if not isinstance(pin, dict) or "owner_profile_id" not in pin:
                continue
            if pin["owner_profile_id"] is not None:
                continue
            if str(pin.get("canonical_source", "")).casefold() != str(details["canonical_source"]).casefold() or str(pin.get("canonical_album_id", "")) != str(details["canonical_album_id"]):
                continue
            source = str(details['canonical_source']).strip()
            release_id = str(details['canonical_album_id']).strip()
            release = deps.fetch_canonical_release(
                canonical_source=source,
                canonical_album_id=release_id,
                album_title=details.get('album_title') or '',
                artist=details.get('artist') or '',
            )
            album = release['album']
            if str(album.get('id')) != release_id:
                continue
            if str(album.get('source') or '').casefold() != source.casefold():
                continue

            tracks = release.get('tracks') or []
            by_id = {}
            for track in tracks:
                if not isinstance(track, dict):
                    continue
                tid = str(track.get('id') or '').strip()
                if tid:
                    by_id.setdefault(tid, []).append(track)

            verified = []
            used = set()
            for missing in details['missing_tracks']:
                if not isinstance(missing, dict):
                    break
                if str(missing.get('source') or '').casefold() != source.casefold():
                    break
                tid = str(missing.get('source_track_id') or '').strip()
                matches = by_id.get(tid, [])
                if len(matches) != 1 or tid in used:
                    break
                track = matches[0]
                if (int(missing.get('track_number') or 0) !=
                        int(track.get('track_number') or 0)):
                    break
                if (int(missing.get('disc_number') or 1) !=
                        int(track.get('disc_number') or 1)):
                    break
                used.add(tid)
                verified.append(track)

            if len(verified) != len(details['missing_tracks']):
                continue

            outcome = deps.queue_album_completion(
                canonical_source=str(details['canonical_source']).strip(),
                canonical_album_id=str(details['canonical_album_id']).strip(),
                album_id=details.get('album_id'),
                album_title=details.get('album_title') or '',
                artist=details.get('artist') or '',
                missing_tracks=verified,
                related_album_ids=details.get('related_album_ids'),
                canonical_album=album,
            )
        except Exception:
            continue

        if isinstance(outcome, dict) and outcome.get('status') == 'queued':
            queued += 1

    return {
        'status': 'completed' if queued else 'skipped',
        'eligible_albums': len(eligible),
        'queued_albums': queued,
    }

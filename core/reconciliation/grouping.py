"""Inventory grouping by verified album membership and folder evidence."""
from __future__ import annotations

import os
import re
from collections import defaultdict

from core.matching.audio_verification import normalize
from core.reconciliation.display import _SOUNDTRACK_SUFFIX
from core.text.fold import fold_title

_SOURCES = {
    'musicbrainz_release_id': 'musicbrainz',
    'spotify_album_id': 'spotify',
    'itunes_album_id': 'itunes',
    'deezer_id': 'deezer',
}


_DISC_FOLDER = re.compile(r'^(?:cd|disc|disk|volume|vol)\s*[-_. ]*\d+\b', re.I)


def _group_disc_folders(folders: dict[str, list[str]], tracks: dict,
                        roots: list[str]) -> dict[str, list[str]]:
    """Treat conventional disc subfolders as one release-level folder audit."""
    groups = {folder: list(paths) for folder, paths in folders.items()}
    by_album: dict[object, list[str]] = defaultdict(list)
    for folder, paths in folders.items():
        ids = {tracks[os.path.normpath(path)]['album_id'] for path in paths
               if os.path.normpath(path) in tracks and
               tracks[os.path.normpath(path)].get('album_id') is not None}
        if len(ids) == 1:
            by_album[next(iter(ids))].append(folder)
    normalized_roots = {os.path.normpath(root) for root in roots}
    for album_folders in by_album.values():
        if len(album_folders) < 2:
            continue
        parent = os.path.commonpath(album_folders)
        if (parent in normalized_roots or
                (parent in groups and parent not in album_folders) or not all(
            folder == parent or
            (os.path.dirname(folder) == parent and _DISC_FOLDER.match(os.path.basename(folder)))
            for folder in album_folders
        )):
            continue
        merged = []
        for folder in album_folders:
            merged.extend(groups.pop(folder))
        groups[parent] = merged
    return groups



def _album_identity_title(value: object) -> str:
    """Album title used only to find conservative soundtrack fragments."""
    title = _SOUNDTRACK_SUFFIX.sub('', str(value or '').strip()).strip(' -–—()[]')
    return normalize(title)



def _album_has_release_id(album: dict | None) -> bool:
    if not album:
        return False
    return bool((album.get('canonical_source') and album.get('canonical_album_id')) or
                any(album.get(column) for column in _SOURCES))



def _group_logical_albums(folders: dict[str, list[str]], tracks: dict,
                           albums: dict) -> tuple[dict[str, list[str]], dict[str, object]]:
    """Join DB-backed folder fragments only when one release is authoritative.

    Folders assigned to the same album row are one logical album. A second,
    narrow rule absorbs tiny soundtrack fragments into one large linked
    soundtrack when titles, owner and unused track positions agree. The
    returned mapping marks the only mixed-ID groups the scanner may accept.
    """
    groups = {folder: list(paths) for folder, paths in folders.items()}

    # Lists only grow during this pass. Cache by identity and length, retaining
    # the list to avoid identity reuse after a group is removed.
    group_facts = {}

    def cached_fact(name, paths, compute):
        key = (name, id(paths))
        previous = group_facts.get(key)
        if previous is None or previous[1] != len(paths):
            previous = (paths, len(paths), compute())
            group_facts[key] = previous
        return previous[2]

    def album_ids(paths):
        return cached_fact('albums', paths, lambda: {tracks[os.path.normpath(path)]['album_id'] for path in paths
                if os.path.normpath(path) in tracks and
                tracks[os.path.normpath(path)].get('album_id') is not None})

    by_album: dict[object, list[str]] = defaultdict(list)
    for folder, paths in groups.items():
        ids = album_ids(paths)
        if len(ids) == 1:
            by_album[next(iter(ids))].append(folder)
    for album_folders in by_album.values():
        if len(album_folders) < 2:
            continue
        representative = min(album_folders,
                             key=lambda folder: (-len(groups[folder]), folder))
        merged = []
        for folder in album_folders:
            merged.extend(groups.pop(folder))
        groups[representative] = merged

    def compute_positions(paths):
        values = set()
        for path in paths:
            row = tracks.get(os.path.normpath(path)) or {}
            try:
                track_number = int(row.get('track_number') or 0)
                disc_number = int(row.get('disc_number') or 1)
            except (TypeError, ValueError):
                return set()
            if track_number <= 0:
                return set()
            values.add((disc_number, track_number))
        return values

    def positions(paths):
        # The soundtrack rule accumulates positions into its own working set.
        return set(cached_fact('positions', paths, lambda: compute_positions(paths)))

    def release_identity(album):
        if album.get('canonical_source') and album.get('canonical_album_id'):
            return album['canonical_source'], str(album['canonical_album_id'])
        for column, source in _SOURCES.items():
            if album.get(column):
                return source, str(album[column])
        return None

    # A box set can be split between files in the release folder and a Disc N
    # child while the media-server scan assigns those two locations different
    # album rows. Join only this physical parent/child shape, with the same
    # normalized album title and at most one authoritative release identity.
    # Position overlap is tolerated only when that release anchor exists:
    # split media-server rows often carry damaged disc numbers, and those are
    # exactly what reconciliation must repair. Without an anchor, positions
    # must remain disjoint to avoid merging coincidentally named copies.
    trusted: dict[str, object] = {}
    # A media-server rescan can split a single physical album into several
    # library rows. Require an exact title/artist/owner and disjoint positions.
    # A shared provider ID can connect rows whose preferred sources differ;
    # conflicting authoritative identities from the same source cannot.
    for folder, paths in groups.items():
        ids = album_ids(paths)
        if len(ids) < 2:
            continue
        rows = [albums.get(album_id) or {} for album_id in ids]
        identities = {release_identity(row) for row in rows if release_identity(row)}
        shared_catalog_id = any(rows[0].get(column) and
                                all(row.get(column) == rows[0][column] for row in rows)
                                for column in _SOURCES)
        compatible_sources = len({source for source, _ in identities}) == len(identities)
        names = {(fold_title(row.get('album_title') or '', drop_brackets=False),
                  fold_title(row.get('artist_name') or '', drop_brackets=False),
                  row.get('owner_profile_id') or 1) for row in rows}
        if (len(names) == 1 and all(next(iter(names))[:2])
                and (len(identities) == 1 or (shared_catalog_id and compatible_sources))
                and len(positions(paths)) == len(paths)):
            anchors = [album_id for album_id in ids if release_identity(albums.get(album_id) or {})]
            trusted[folder] = min(anchors, key=lambda album_id: (
                not albums[album_id].get('canonical_locked'),
                not albums[album_id].get('canonical_source'),
                not albums[album_id].get('musicbrainz_release_id'), str(album_id)))
    for child in sorted(list(groups)):
        if child not in groups or not _DISC_FOLDER.match(os.path.basename(child)):
            continue
        parent = os.path.dirname(child)
        if parent not in groups:
            continue
        parent_ids, child_ids = album_ids(groups[parent]), album_ids(groups[child])
        if len(parent_ids) != 1 or len(child_ids) != 1 or parent_ids == child_ids:
            continue
        parent_id, child_id = next(iter(parent_ids)), next(iter(child_ids))
        parent_album, child_album = albums.get(parent_id) or {}, albums.get(child_id) or {}
        if (_album_identity_title(parent_album.get('album_title')) !=
                _album_identity_title(child_album.get('album_title'))):
            continue

        identities = {identity for identity in
                      (release_identity(parent_album), release_identity(child_album))
                      if identity}
        if len(identities) > 1:
            continue
        parent_positions, child_positions = positions(groups[parent]), positions(groups[child])
        if (not parent_positions or not child_positions or
                (parent_positions & child_positions and not identities)):
            continue
        anchor_id = (child_id if release_identity(child_album) and
                     not release_identity(parent_album) else parent_id)
        groups[parent].extend(groups.pop(child))
        trusted[parent] = anchor_id

    # Replacement downloads can be imported as a new album row in a sibling
    # folder, sometimes with an extra edition qualifier on the original row.
    # Join only a one-file gap filler to one nearby release-identified anchor.
    def base_title(album):
        return normalize(re.split(r'\s*[\[(]', str(album.get('album_title') or ''), maxsplit=1)[0])

    def sibling_key(folder, album):
        return (os.path.dirname(folder), base_title(album),
                normalize(album.get('artist_name') or ''),
                album.get('owner_profile_id') or 1)

    sibling_groups = defaultdict(list)
    for folder, paths in groups.items():
        ids = album_ids(paths)
        if len(ids) == 1:
            sibling_groups[sibling_key(folder, albums.get(next(iter(ids))) or {})].append(folder)

    def siblings(folder, album):
        # Preserve original dict order and all ambiguous candidates. Merges
        # only remove keys; existing checks reject groups that became mixed-ID.
        for candidate in sibling_groups.get(sibling_key(folder, album), ()):
            if candidate in groups:
                yield candidate, groups[candidate]

    for fragment in sorted(list(groups)):
        if fragment not in groups or len(groups[fragment]) != 1:
            continue
        ids = album_ids(groups[fragment])
        if len(ids) != 1:
            continue
        fragment_id = next(iter(ids))
        fragment_album = albums.get(fragment_id) or {}
        fragment_positions = positions(groups[fragment])
        if not fragment_positions or not base_title(fragment_album):
            continue
        candidates = []
        for anchor, paths in siblings(fragment, fragment_album):
            if (anchor == fragment or len(paths) < 3 or
                    os.path.dirname(anchor) != os.path.dirname(fragment)):
                continue
            anchor_ids = album_ids(paths)
            if len(anchor_ids) != 1 or fragment_id in anchor_ids:
                continue
            anchor_id = next(iter(anchor_ids))
            anchor_album = albums.get(anchor_id) or {}
            if (not _album_has_release_id(anchor_album) or
                    base_title(anchor_album) != base_title(fragment_album) or
                    normalize(anchor_album.get('artist_name') or '') !=
                    normalize(fragment_album.get('artist_name') or '') or
                    (anchor_album.get('owner_profile_id') or 1) !=
                    (fragment_album.get('owner_profile_id') or 1)):
                continue
            anchor_identity = release_identity(anchor_album)
            fragment_identity = release_identity(fragment_album)
            # Downloaders may tag a replacement track with a different
            # edition's release ID. A shared provider album ID still proves
            # these sibling folders belong to the same album. Require the
            # title, artist, unused position, and one-file checks above too.
            shared_catalog_id = any(
                anchor_album.get(column) and
                anchor_album.get(column) == fragment_album.get(column)
                for column in _SOURCES
            )
            if (fragment_identity and fragment_identity != anchor_identity and
                    not shared_catalog_id):
                continue
            anchor_positions = positions(paths)
            if anchor_positions and not fragment_positions & anchor_positions:
                candidates.append((anchor, anchor_id))
        if len(candidates) == 1:
            anchor, anchor_id = candidates[0]
            groups[anchor].extend(groups.pop(fragment))
            trusted[anchor] = anchor_id

    # A digital deluxe addendum can arrive as a second media-server album:
    # the regular folder holds the opening sequence and the deluxe folder only
    # higher-numbered extras. Join this narrow, position-disjoint shape so the
    # release comparison sees the whole owned collection. The deluxe release
    # remains a review choice; no file is retagged by this grouping step.
    for deluxe_folder in sorted(list(groups)):
        if deluxe_folder not in groups:
            continue
        deluxe_ids = album_ids(groups[deluxe_folder])
        if len(deluxe_ids) != 1 or len(groups[deluxe_folder]) < 2:
            continue
        deluxe_id = next(iter(deluxe_ids))
        deluxe_album = albums.get(deluxe_id) or {}
        title = str(deluxe_album.get('album_title') or '')
        if (not re.search(r'\bdeluxe\b', title, re.I) or
                not _album_has_release_id(deluxe_album)):
            continue
        extra_positions = positions(groups[deluxe_folder])
        if len(extra_positions) != len(groups[deluxe_folder]):
            continue
        candidates = []
        for regular_folder, regular_paths in siblings(deluxe_folder, deluxe_album):
            if (regular_folder == deluxe_folder or len(regular_paths) < 8 or
                    os.path.dirname(regular_folder) != os.path.dirname(deluxe_folder)):
                continue
            regular_ids = album_ids(regular_paths)
            if len(regular_ids) != 1 or deluxe_id in regular_ids:
                continue
            regular_album = albums.get(next(iter(regular_ids))) or {}
            regular_title = str(regular_album.get('album_title') or '')
            if (re.search(r'\bdeluxe\b', regular_title, re.I) or
                    base_title(regular_album) != base_title(deluxe_album) or
                    normalize(regular_album.get('artist_name') or '') !=
                    normalize(deluxe_album.get('artist_name') or '') or
                    (regular_album.get('owner_profile_id') or 1) !=
                    (deluxe_album.get('owner_profile_id') or 1)):
                continue
            regular_positions = positions(regular_paths)
            count = len(regular_paths)
            if (regular_positions == {(1, number) for number in range(1, count + 1)} and
                    extra_positions and
                    all(disc == 1 and count < number <= count * 3
                        for disc, number in extra_positions) and
                    min(number for _, number in extra_positions) == count + 1):
                candidates.append(regular_folder)
        if len(candidates) == 1:
            regular_folder = candidates[0]
            groups[deluxe_folder].extend(groups.pop(regular_folder))
            trusted[deluxe_folder] = deluxe_id

    buckets: dict[tuple[object, str], list[str]] = defaultdict(list)
    for folder, paths in groups.items():
        ids = album_ids(paths)
        if len(ids) != 1:
            continue
        album = albums.get(next(iter(ids))) or {}
        key = _album_identity_title(album.get('album_title'))
        if key:
            buckets[(album.get('owner_profile_id') or 1, key)].append(folder)

    for bucket in buckets.values():
        anchors = []
        for folder in bucket:
            if folder not in groups:
                continue
            ids = album_ids(groups[folder])
            album_id = next(iter(ids))
            album = albums.get(album_id) or {}
            title = str(album.get('album_title') or '')
            folder_signals_soundtrack = any(
                'soundtrack' in os.path.dirname(path).casefold()
                for path in groups[folder]
            )
            if (len(groups[folder]) >= 8 and _album_has_release_id(album) and
                    ('soundtrack' in title.casefold() or folder_signals_soundtrack or
                     str(album.get('record_type') or '').casefold() == 'compilation')):
                anchors.append((folder, album_id))
        if len(anchors) != 1:
            continue
        anchor_folder, anchor_id = anchors[0]
        anchor_album = albums.get(anchor_id) or {}
        anchor_positions = positions(groups[anchor_folder])
        if not anchor_positions:
            continue
        fragments = []
        for folder in bucket:
            if folder == anchor_folder:
                continue
            paths = groups[folder]
            ids = album_ids(paths)
            fragment_id = next(iter(ids))
            fragment_album = albums.get(fragment_id) or {}
            fragment_positions = positions(paths)
            if (len(paths) <= 3 and not _album_has_release_id(fragment_album) and
                    (fragment_album.get('owner_profile_id') or 1) ==
                    (anchor_album.get('owner_profile_id') or 1) and
                    fragment_positions and not (anchor_positions & fragment_positions)):
                fragments.append(folder)
                anchor_positions.update(fragment_positions)
        if fragments:
            for folder in fragments:
                groups[anchor_folder].extend(groups.pop(folder))
            trusted[anchor_folder] = anchor_id
    return groups, trusted



def _group_unassigned_downloads(folders, tracks, albums, trusted, read_tags):
    """Include sibling download fragments with unanimous exact album/artist tags.

    This only establishes album context. The joint audio assignment still checks
    every recording, duration and version and retains extras/duplicates for review.
    Multiple possible owners or incomplete tags leave the fragment separate.
    """
    def identity(value):
        # Audio-title normalization removes edition qualifiers; folder ownership
        # must retain them so a regular and deluxe release cannot collide.
        return ' '.join(str(value or '').casefold().split())

    anchors = defaultdict(list)
    for folder, paths in folders.items():
        ids = {tracks[os.path.normpath(path)].get('album_id') for path in paths
               if os.path.normpath(path) in tracks}
        ids.discard(None)
        album_id = trusted.get(folder) if folder in trusted else next(iter(ids)) if len(ids) == 1 else None
        album = albums.get(album_id) or {}
        if album_id is not None and _album_has_release_id(album):
            anchors[os.path.dirname(folder)].append((folder, album_id, album))
    groups = {folder: list(paths) for folder, paths in folders.items()}
    for folder, paths in folders.items():
        possible = anchors.get(os.path.dirname(folder), [])
        if not possible or any(os.path.normpath(path) in tracks for path in paths):
            continue
        identities = {(identity(tags.get('album')),
                       identity(tags.get('album_artist') or tags.get('artist')))
                      for tags in (read_tags(path) for path in paths)}
        if len(identities) != 1 or any(not value for value in next(iter(identities))):
            continue
        matches = [(anchor, album_id) for anchor, album_id, album in possible
                   if (identity(album.get('album_title')),
                       identity(album.get('artist_name'))) in identities]
        if len(matches) == 1:
            anchor, album_id = matches[0]
            groups[anchor].extend(groups.pop(folder))
            trusted[anchor] = album_id
    return groups


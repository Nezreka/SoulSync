"""Metadata-aware file grouping, cached probes, and move fingerprints."""
from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from core.audiobook_library_metadata import read_metadata
from core.audiobook_organizer import AUDIO_EXTENSIONS, natural_key

DISC = re.compile(r'^(?:cd|disc|disk|part)\s*[-_ ]*\d+$', re.I)
CHAPTER = re.compile(r'^(?:(.*?)\s*[-_ .]*\b(?:chapter|track|part|disc|cd)\s*\d+.*|\d{1,4}\s*[-_. ]+.*)$', re.I)
MIN_BYTES = 1024 * 1024


def canonical(path):
    return os.path.normcase(str(Path(path).resolve()))


def folded(value):
    return re.sub(r'\W+', ' ', str(value or '').casefold()).strip()


@dataclass
class BookGroup:
    path: Path
    files: list[Path]
    probes: list[dict]
    scope: str
    grouping: str
    fingerprint: str
    signature: str


def probe_file(path, db):
    stat = path.stat()
    key = canonical(path)
    cached = db.cached_library_file(key, stat.st_size, stat.st_mtime_ns)
    if isinstance(cached, dict):
        return cached
    facts = read_metadata(path, [path], include_sidecars=False)
    digest = hashlib.sha256(str(stat.st_size).encode())
    # Sampling supports move detection without hashing gigabytes on each scan.
    with path.open('rb') as stream:
        for offset in sorted({0, max(0, stat.st_size // 2 - 32768), max(0, stat.st_size - 65536)}):
            stream.seek(offset)
            digest.update(stream.read(65536))
    facts['fingerprint'] = digest.hexdigest()
    facts['size'] = stat.st_size
    facts['mtime'] = stat.st_mtime_ns
    db.cache_library_file(key, stat.st_size, stat.st_mtime_ns, facts)
    return facts


def discover(root: Path, db, error, max_depth=32):
    containers = defaultdict(list)
    stack = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        if depth > max_depth:
            error(folder, 'Folder nesting exceeds the scan limit')
            continue
        try:
            entries = sorted(folder.iterdir())
            audio = [p for p in entries if not p.is_symlink() and p.is_file()
                     and not p.name.startswith('.') and p.suffix.lower() in AUDIO_EXTENSIONS]
            container = folder.parent if DISC.fullmatch(folder.name) and folder.parent != root else folder
            for file in audio:
                containers[container].append((file, probe_file(file, db)))
            for child in entries:
                if child.is_symlink() or child.name.startswith('.') or not child.is_dir():
                    continue
                if audio and child.name.casefold() in {'extras', 'samples', 'sample', 'bonus'}:
                    continue
                stack.append((child, depth + 1))
        except OSError as exc:
            error(folder, str(exc))

    groups = []
    for container, entries in containers.items():
        grouped = defaultdict(list)
        sidecar = read_metadata(container, [], probes=[])
        for file, facts in entries:
            if facts.get('title_source') == 'metadata':
                # Different narrators, authors or explicit IDs remain separate editions.
                title = facts['title']
                if DISC.fullmatch(file.parent.name):
                    title = re.sub(r'\s*[\[(]?(?:disc|cd)\s*\d+(?:\s*(?:of|/)\s*\d+)?[\])]?$', '', title, flags=re.I)
                peers = [f for _, f in entries if folded(f.get('title')) == folded(facts['title'])]
                def consistent(field):
                    values = {f.get(field) for f in peers if f.get(field)}
                    return facts.get(field) or (next(iter(values)) if len(values) == 1 else '') or sidecar.get(field) or ''
                identity = ('book', folded(title), folded(consistent('author')),
                            folded(consistent('narrator')), consistent('asin'))
                reason = 'Grouped by book title, author, narrator and identifier in file metadata'
            elif sidecar.get('title_source') == 'metadata':
                identity = ('book', folded(sidecar['title']), folded(sidecar['author']), folded(sidecar['narrator']), sidecar.get('asin', ''))
                reason = 'Grouped by folder sidecar metadata'
            else:
                chapter = CHAPTER.match(file.stem)
                if chapter and (container != root or chapter.group(1)):
                    identity = ('chapters', folded(chapter.group(1) or container.name))
                    reason = 'Numbered chapter sequence; catalogue identity still needs evidence'
                else:
                    identity = ('file', file.name)
                    reason = 'Standalone file; no reliable chapter grouping metadata'
            grouped[identity].append((file, facts, reason))
        for items in grouped.values():
            files = sorted((item[0] for item in items), key=lambda p: natural_key(str(p)))
            if sum(p.stat().st_size for p in files) < MIN_BYTES:
                continue
            probes = [next(item[1] for item in items if item[0] == file) for file in files]
            # Never own a shared parent: deleting one group must not delete its neighbours.
            owns_folder = len(grouped) == 1 and container != root and not any(
                other != container and other.is_relative_to(container) for other in containers)
            path = container if owns_folder else files[0]
            fingerprint = hashlib.sha256('\n'.join(sorted(p['fingerprint'] for p in probes)).encode()).hexdigest()
            signature_parts = [f'{p}:{facts["size"]}:{facts["mtime"]}' for p, facts in zip(files, probes)]
            for sidecar_path in ([container / name for name in ('metadata.opf', 'book.nfo', 'metadata.json')]
                                 + [files[0].with_suffix('.opf'), files[0].with_suffix('.nfo')]):
                if sidecar_path.exists():
                    stat = sidecar_path.stat()
                    signature_parts.append(f'{sidecar_path}:{stat.st_size}:{stat.st_mtime_ns}')
            signature = hashlib.sha256('\n'.join(signature_parts).encode()).hexdigest()
            groups.append(BookGroup(path, files, probes, 'folder' if owns_folder else 'files',
                                    items[0][2], fingerprint, signature))
    return groups

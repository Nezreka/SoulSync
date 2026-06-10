"""Confusable-tolerant filesystem path resolution (#833).

the-hang-man: a track titled "I'm Upset" was written to disk with an ASCII
apostrophe (U+0027) but the library DB stored the title with a typographic one
(U+2019, the form Spotify/Apple metadata uses). Deleting rebuilt the unlink
target from the DB path, so ``os.path.exists`` compared U+2019-bytes against a
U+0027 filename — always a miss — and the file survived ("could not be deleted").

The same byte-exact mismatch hits any on-disk operation that starts from stored
metadata (delete, sidecar cleanup, dead-file checks). The fix is to resolve the
*real* on-disk name: descend the path component by component, taking an exact
match when present and otherwise folding a small set of typographic confusables
(curly vs straight quotes, en/em dash, ellipsis, nbsp) for the comparison ONLY.
We never rename — we just find the file that's actually there.

Case is deliberately preserved: on a case-sensitive dataset (ext4/ZFS) two
tracks can differ only by case, so folding case could delete the wrong file.
The reported failure is purely typographic, so that's all we fold.
"""

from __future__ import annotations

import os
import unicodedata

# Typographic characters that routinely differ between DB metadata (Unicode,
# from streaming-service catalogs) and the ASCII filename on disk. Folded to a
# common form for COMPARISON only.
_CONFUSABLES = {
    '‘': "'", '’': "'", 'ʼ': "'", '′': "'",   # ‘ ’ ʼ ′ → '
    '“': '"', '”': '"', '″': '"',                   # “ ” ″ → "
    '–': '-', '—': '-', '‒': '-', '―': '-',    # – — ‒ ― → -
    '…': '...',                                                # … → ...
    ' ': ' ',                                                  # nbsp → space
}


def fold_confusables(name: str) -> str:
    """Fold typographic confusables + NFC-normalize so a DB name and the real
    on-disk name compare equal despite curly-vs-straight quotes, dashes, etc.
    Case and everything else are left untouched."""
    if not name:
        return ''
    name = unicodedata.normalize('NFC', name)
    for bad, good in _CONFUSABLES.items():
        if bad in name:
            name = name.replace(bad, good)
    return name


def find_on_disk(base_dir: str, suffix_parts):
    """Descend ``base_dir`` following ``suffix_parts`` (the path components of a
    stored file path). Each component is matched exactly when it exists, else by
    confusable-folded comparison against the directory's real entries. Returns
    the real absolute path, or None if any component can't be resolved.

    Exact matches always win — the folded scan only runs for a component that
    isn't present byte-for-byte, so this never changes behaviour for paths that
    already resolve.
    """
    if not base_dir or not os.path.isdir(base_dir):
        return None
    current = base_dir
    for part in suffix_parts:
        if not part:
            continue
        exact = os.path.join(current, part)
        if os.path.exists(exact):
            current = exact
            continue
        target = fold_confusables(part)
        match = None
        try:
            for entry in os.listdir(current):
                if fold_confusables(entry) == target:
                    match = os.path.join(current, entry)
                    break
        except OSError:
            return None
        if match is None:
            return None
        current = match
    return current if current != base_dir else None


__all__ = ['fold_confusables', 'find_on_disk']

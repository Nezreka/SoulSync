"""$label in the Album Path Template (Goosebro's request).

The record label (MusicBrainz label-info, first named label) was already
parsed into Album.label and already flowed through the metadata layer —
it just wasn't exposed as a template variable. This wires $label /
${label} into the album path renderer, mirroring $disambiguation (#1352).
"""

from __future__ import annotations

import core.imports.paths as import_paths


def _ctx(label=None):
    ctx = {
        "albumartist": "Aphex Twin",
        "artist": "Aphex Twin",
        "album": "Selected Ambient Works 85-92",
        "title": "Xtal",
        "track_number": 1,
        "disc_number": 1,
    }
    if label is not None:
        ctx["label"] = label
    return ctx


def test_label_variable_substitutes():
    folder, filename = import_paths.get_file_path_from_template_raw(
        "$albumartist/$label/$album/$track - $title", _ctx("Apollo")
    )
    assert folder == "Aphex Twin/Apollo/Selected Ambient Works 85-92"
    assert filename == "01 - Xtal"


def test_label_bracket_form_substitutes():
    folder, _ = import_paths.get_file_path_from_template_raw(
        "$albumartist/${label}/$album/$track - $title", _ctx("Apollo")
    )
    assert folder == "Aphex Twin/Apollo/Selected Ambient Works 85-92"


def test_empty_label_drops_segment_cleanly():
    folder, _ = import_paths.get_file_path_from_template_raw(
        "$albumartist/$label/$album/$track - $title", _ctx("")
    )
    assert folder == "Aphex Twin/Selected Ambient Works 85-92"


def test_missing_label_drops_segment_cleanly():
    folder, _ = import_paths.get_file_path_from_template_raw(
        "$albumartist/$label/$album/$track - $title", _ctx()
    )
    assert folder == "Aphex Twin/Selected Ambient Works 85-92"


def test_label_sanitized_for_filesystem():
    folder, _ = import_paths.get_file_path_from_template_raw(
        "$label/$album/$track - $title", _ctx("Warp: Records?")
    )
    # sanitize_filename strips characters illegal on filesystems
    assert ":" not in folder and "?" not in folder
    assert "Warp" in folder

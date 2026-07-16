"""Settings paths UX (Discord: Kazimir Iskander + wolf39us).

The through-line that misled non-Docker users: a "don't touch" warning sat on
the Download/Music-Library fields (which Proxmox-LXC/bare-metal users MUST
edit), while the section below described itself exactly like the output folder
— so people set "Music Library Paths" and never touched the real output. The
copy now: explains the download → post-process → library pipeline, addresses
Docker AND non-Docker explicitly (Docker is not assumed to be the norm), and
the extra-libraries section leads with "you don't need to repeat it here".
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_DOCS = (_ROOT / "webui" / "static" / "docs.js").read_text(encoding="utf-8")
_WIZ = (_ROOT / "webui" / "static" / "setup-wizard.js").read_text(encoding="utf-8")


def test_pipeline_explained_on_the_folder_fields():
    assert "How files flow:" in _INDEX
    assert "post-processing" in _INDEX
    assert "Keeping the two separate stops half-finished files being scanned." in _INDEX


def test_warning_serves_both_audiences_not_just_docker():
    # the old copy told EVERYONE to leave the fields alone; LXC/bare-metal
    # users are the ones who must edit them
    assert "Container-internal paths. Only change them if you know what you're doing" not in _INDEX
    assert "Docker / Unraid:" in _INDEX
    assert "Proxmox LXC" in _INDEX
    assert "expected to" in _INDEX and "unlock" in _INDEX


def test_labels_dropped_the_ambiguous_transfer_language():
    assert "Download Folder (input):" in _INDEX
    assert "Music Library Folder (output):" in _INDEX
    assert "Input Folder (Download Dir):" not in _INDEX
    assert "Output Folder (Music Library):" not in _INDEX


def test_extra_libraries_section_stops_being_a_red_herring():
    assert "<h3>Additional Music Libraries</h3>" in _INDEX
    assert "<h3>Music Library Paths</h3>" not in _INDEX
    assert "you don't need to repeat it here" in _INDEX


def test_docs_and_wizard_tell_the_same_story():
    assert "Additional Music Libraries" in _DOCS
    assert "Music Library Folder" in _WIZ and "Download Folder" in _WIZ

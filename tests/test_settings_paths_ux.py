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
# Library > Folders (settings-bic phase 4); its copy is checked
# with the whitespace JSX wraps lines with collapsed
_FOLDERS = (_ROOT / "webui" / "src" / "features" / "settings" / "tabs" / "library-folders.tsx").read_text(
    encoding="utf-8")
_FOLDERS_FLAT = " ".join(_FOLDERS.split())
_DOCS = " ".join(
    p.read_text(encoding="utf-8")
    for p in sorted((_ROOT / "webui" / "static" / "docs-content").glob("*.js"))
)
# the setup flow (react since oct 2026) names the folders in one place
_SETUP = (_ROOT / "webui" / "src" / "features" / "onboarding" / "plan.ts").read_text(encoding="utf-8")


def test_pipeline_explained_on_the_folder_fields():
    assert "How files flow:" in _FOLDERS_FLAT
    assert "post-processing" in _FOLDERS_FLAT
    assert "Keeping the two separate stops half-finished files being scanned." in _FOLDERS_FLAT


def test_warning_serves_both_audiences_not_just_docker():
    # the old copy told EVERYONE to leave the fields alone; LXC/bare-metal
    # users are the ones who must edit them
    for ui in (_INDEX, _FOLDERS_FLAT):
        assert "Container-internal paths. Only change them if you know what you're doing" not in ui
    assert "Docker / Unraid detected:" in _FOLDERS_FLAT
    assert "Proxmox LXC" in _FOLDERS_FLAT
    assert "expected to" in _FOLDERS_FLAT and "unlock" in _FOLDERS_FLAT


def test_labels_dropped_the_ambiguous_transfer_language():
    # sentence case, no colon (settings-bic phase 4); the flow line says which
    # is the input and which the output
    assert 'label="Download folder"' in _FOLDERS
    assert 'label="Music library folder"' in _FOLDERS
    assert "<b>Download folder</b>" in _FOLDERS and "<b>Music library</b>" in _FOLDERS
    for ui in (_INDEX, _FOLDERS):
        assert "Input Folder (Download Dir):" not in ui
        assert "Output Folder (Music Library):" not in ui


def test_extra_libraries_section_stops_being_a_red_herring():
    assert 'title="Additional music libraries"' in _FOLDERS
    for ui in (_INDEX, _FOLDERS):
        assert "<h3>Music Library Paths</h3>" not in ui
    assert "need to repeat it here" in _FOLDERS_FLAT


def test_docs_and_setup_tell_the_same_story():
    assert "Additional Music Libraries" in _DOCS
    # setup says downloads and music library, never the old transfer words
    assert "name: 'Downloads'" in _SETUP and "name: 'Music library'" in _SETUP
    assert "Transfer" not in _SETUP.split("FOLDER_INFO", 1)[1].split("};", 1)[0]


def test_podcasts_folder_field_present():
    assert 'label="Podcasts folder"' in _FOLDERS
    assert 'id="podcasts-path"' in _FOLDERS
    # behind its Unlock button, like the other folders
    field = _FOLDERS.split('id="podcasts-path"', 1)[0].rsplit("<LibPath", 1)[1]
    # nothing closes between the LibPath tag and the id: the id is its prop
    assert "/>" not in field and "</" not in field


def test_podcast_path_template_field_present():
    # Library > Naming, Podcasts (settings-bic phase 4)
    card = (Path(__file__).resolve().parent.parent / "webui/src/features/settings/tabs"
            / "library-organization.tsx").read_text(encoding="utf-8")
    assert 'label="Podcast path template"' in card
    assert 'id="template-podcast-path"' in card
    assert 'placeholder="$show/Season $season/$title"' in card



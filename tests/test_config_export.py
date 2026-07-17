"""Config export/import bundle (Kazimir's migration "checkout" menu).

One JSON bundle for both sides. Secrets redacted by default; a redacted
bundle imported back never blanks existing credentials (the config_manager
guard skips the mask). Non-bundles are rejected.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.config_export import (
    BUNDLE_MARKER,
    apply_bundle,
    build_bundle,
    validate_bundle,
)

_ROOT = Path(__file__).resolve().parent.parent


class _CM:
    REDACTED_SENTINEL = "__redacted_unchanged__"

    def __init__(self):
        self.config_data = {
            "active_media_server": "plex",
            "spotify": {"client_id": "REALID", "client_secret": "SECRET"},
            "plex": {"base_url": "http://plex", "token": "PTOK"},
        }
        self.written = {}

    def get_full_config(self):
        import copy
        return copy.deepcopy(self.config_data)

    def redacted_config(self):
        import copy
        d = copy.deepcopy(self.config_data)
        d["spotify"]["client_id"] = self.REDACTED_SENTINEL
        d["spotify"]["client_secret"] = self.REDACTED_SENTINEL
        d["plex"]["token"] = self.REDACTED_SENTINEL
        return d

    def set(self, path, value):
        if value == self.REDACTED_SENTINEL:
            return                      # the real guard: never write a mask
        self.written[path] = value

    def apply_config_dict(self, incoming):
        n = 0

        def walk(node, pre):
            nonlocal n
            for k, v in (node or {}).items():
                p = f"{pre}.{k}" if pre else k
                if isinstance(v, dict) and v:
                    walk(v, p)
                else:
                    self.set(p, v)
                    n += 1
        walk(incoming, "")
        return n


class _VDB:
    def __init__(self):
        self.store = {"quality_profiles": '{"0":{}}', "organization": '{"x":1}',
                      "studio_network_links_seeded": "1"}

    def all_video_settings(self, exclude=frozenset()):
        return {k: (json.loads(v) if isinstance(v, str) else v)
                for k, v in self.store.items() if k not in exclude}

    def replace_video_settings(self, s):
        for k, v in s.items():
            self.store[k] = v if isinstance(v, str) else json.dumps(v)
        return len(s)


def test_redacted_export_masks_secrets():
    b = build_bundle(_CM(), _VDB(), include_secrets=False, exported_at="T")
    assert b[BUNDLE_MARKER] is True and b["includes_secrets"] is False
    assert b["music"]["spotify"]["client_id"] == "__redacted_unchanged__"
    assert b["music"]["plex"]["base_url"] == "http://plex"    # non-secret kept


def test_full_export_embeds_real_secrets():
    b = build_bundle(_CM(), _VDB(), include_secrets=True, exported_at="T")
    assert b["includes_secrets"] is True
    assert b["music"]["spotify"]["client_secret"] == "SECRET"


def test_export_excludes_internal_video_flags():
    b = build_bundle(_CM(), _VDB(), include_secrets=False, exported_at="T")
    assert "quality_profiles" in b["video"]
    assert "studio_network_links_seeded" not in b["video"]     # one-time flag dropped


def test_importing_a_redacted_bundle_never_blanks_secrets():
    b = build_bundle(_CM(), _VDB(), include_secrets=False, exported_at="T")
    target = _CM()
    summary = apply_bundle(target, _VDB(), b)
    assert summary["music_keys"] > 0
    # the redacted secret masks were skipped, real non-secrets applied
    assert "spotify.client_id" not in target.written
    assert target.written.get("plex.base_url") == "http://plex"


def test_full_bundle_imports_real_secrets():
    b = build_bundle(_CM(), _VDB(), include_secrets=True, exported_at="T")
    target = _CM()
    apply_bundle(target, _VDB(), b)
    assert target.written.get("spotify.client_secret") == "SECRET"


def test_video_settings_round_trip():
    b = build_bundle(_CM(), _VDB(), include_secrets=True, exported_at="T")
    vdb = _VDB()
    vdb.store = {}
    apply_bundle(_CM(), vdb, b)
    assert json.loads(vdb.store["quality_profiles"]) == {"0": {}}


@pytest.mark.parametrize("bad,reason_frag", [
    ("not a dict", "JSON object"),
    ({"random": "json"}, "isn't a SoulSync config export"),
    ({BUNDLE_MARKER: True, "bundle_version": 99, "music": {}, "video": {}}, "newer SoulSync"),
    ({BUNDLE_MARKER: True, "bundle_version": 1, "music": "x", "video": {}}, "missing its music"),
])
def test_validate_rejects_bad_bundles(bad, reason_frag):
    ok, reason = validate_bundle(bad)
    assert ok is False and reason_frag in reason


def test_apply_rejects_a_non_bundle():
    with pytest.raises(ValueError):
        apply_bundle(_CM(), _VDB(), {"random": "json"})


# ── wiring contracts ──────────────────────────────────────────────────────────

def test_endpoints_and_ui_are_wired():
    src = (_ROOT / "web_server.py").read_text(encoding="utf-8", errors="replace")
    assert "'/api/config/export'" in src and "'/api/config/import'" in src
    assert "from core.config_export import build_bundle" in src
    js = (_ROOT / "webui" / "static" / "config-migration.js").read_text(encoding="utf-8", errors="replace")
    html = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8", errors="replace")
    assert "openConfigExportModal" in js and "openConfigExportModal" in html
    assert "config-migration.js" in html
    # copy + save + credentials toggle + import all present
    assert "cfgx-copy" in js and "cfgx-save" in js and "cfgx-secrets" in js and "/api/config/import" in js

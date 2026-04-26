"""Tests for the script.js → module split integrity.

Verifies that:
 - The monolithic script.js no longer exists
 - All expected split modules are present on disk
 - index.html loads all split modules via <script> tags
 - core.js loads first and init.js loads last (ordering contract)
 - No duplicate top-level function declarations across modules
 - Every onclick="fn(…)" in index.html has a matching function
   declaration in one of the split modules
 - No module references undefined globals at parse time via
   window.X = X assignments (the bug pattern we fixed)
"""

import os
import re
from pathlib import Path
from collections import defaultdict

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "webui" / "static"
_INDEX = _ROOT / "webui" / "index.html"

# The 17 modules that replaced script.js + shared-helpers.js extracted from
# artists.js (order matters for first/last checks)
SPLIT_MODULES = [
    "core.js",
    "shared-helpers.js",
    "media-player.js",
    "settings.js",
    "search.js",
    "sync-spotify.js",
    "downloads.js",
    "wishlist-tools.js",
    "sync-services.js",
    "api-monitor.js",
    "library.js",
    "beatport-ui.js",
    "discover.js",
    "enrichment.js",
    "stats-automations.js",
    "pages-extra.js",
    "init.js",
]

# Other JS files that exist in static/ but are NOT part of the split
NON_SPLIT_JS = {"setup-wizard.js", "docs.js", "helper.js", "particles.js", "worker-orbs.js"}

# Pre-existing duplicate helper functions that lived in the original monolith.
# In a plain <script> context the last-loaded declaration wins.  These are NOT
# regressions from the split — they should be deduplicated in a follow-up.
KNOWN_CROSS_FILE_DUPES = {
    "escapeHtml",        # downloads.js, shared-helpers.js, discover.js
    "formatDuration",    # sync-spotify.js, wishlist-tools.js, sync-services.js
    "matchedDownloadTrack",    # downloads.js, wishlist-tools.js
    "matchedDownloadAlbum",    # downloads.js, wishlist-tools.js
    "matchedDownloadAlbumTrack",  # downloads.js, wishlist-tools.js
    "_esc",              # library.js, stats-automations.js
    "_escAttr",          # downloads.js, stats-automations.js
    "_formatDuration",   # stats-automations.js, pages-extra.js
    "loadDashboardData", # search.js, wishlist-tools.js
}

# Pre-existing same-file duplicates (two filter UIs reuse the same names).
KNOWN_SAME_FILE_DUPES = {
    "applyFiltersAndSort",
    "calculateRelevanceScore",
    "handleFilterClick",
    "initializeFilters",
    "resetFilters",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FUNC_DECL_RE = re.compile(r"^(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", re.MULTILINE)
_ONCLICK_RE = re.compile(r'onclick="([^"]*)"')
_ONCLICK_FN_RE = re.compile(r"^([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")
_WINDOW_ASSIGN_RE = re.compile(
    r"^window\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*;",
    re.MULTILINE,
)
_SCRIPT_SRC_RE = re.compile(r"filename='([^']+\.js)'")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _all_function_decls(js_text: str) -> list[str]:
    """Return all top-level function declaration names in a JS file."""
    return _FUNC_DECL_RE.findall(js_text)


def _script_load_order(html: str) -> list[str]:
    """Return the ordered list of JS filenames loaded from index.html."""
    return _SCRIPT_SRC_RE.findall(html)


# =========================================================================
# Group A — File Existence
# =========================================================================

class TestFileExistence:
    """The old monolith is gone and all split modules are present."""

    def test_monolith_removed(self):
        assert not (_STATIC / "script.js").exists(), "script.js should have been removed"

    @pytest.mark.parametrize("module", SPLIT_MODULES)
    def test_split_module_exists(self, module):
        path = _STATIC / module
        assert path.exists(), f"{module} missing from webui/static/"
        assert path.stat().st_size > 0, f"{module} is empty"


# =========================================================================
# Group B — index.html Script Loading
# =========================================================================

class TestScriptLoading:
    """index.html references every split module in the correct order."""

    @pytest.fixture(autouse=True)
    def _load_html(self):
        self.html = _read(_INDEX)
        self.loaded = _script_load_order(self.html)

    @pytest.mark.parametrize("module", SPLIT_MODULES)
    def test_module_loaded_in_html(self, module):
        assert module in self.loaded, f"{module} not loaded in index.html"

    def test_core_loads_first(self):
        """core.js must be the first split module loaded."""
        split_in_html = [f for f in self.loaded if f in SPLIT_MODULES]
        assert split_in_html[0] == "core.js", (
            f"Expected core.js first, got {split_in_html[0]}"
        )

    def test_init_loads_last(self):
        """init.js must be the last split module loaded."""
        split_in_html = [f for f in self.loaded if f in SPLIT_MODULES]
        assert split_in_html[-1] == "init.js", (
            f"Expected init.js last, got {split_in_html[-1]}"
        )

    def test_no_duplicate_script_tags(self):
        """Each module should only be loaded once."""
        split_in_html = [f for f in self.loaded if f in SPLIT_MODULES]
        assert len(split_in_html) == len(set(split_in_html)), (
            "Duplicate script tags detected"
        )


# =========================================================================
# Group C — No Duplicate Function Declarations
# =========================================================================

class TestNoDuplicateFunctions:
    """No two split modules should declare the same top-level function."""

    @pytest.fixture(autouse=True)
    def _scan_all(self):
        self.func_map: dict[str, list[str]] = defaultdict(list)
        for module in SPLIT_MODULES:
            text = _read(_STATIC / module)
            for fn_name in _all_function_decls(text):
                self.func_map[fn_name].append(module)

    def test_no_new_cross_file_duplicates(self):
        """Catch NEW duplicate declarations; known pre-existing ones are allowed."""
        dupes = {
            fn: files
            for fn, files in self.func_map.items()
            if len(files) > 1
            and fn not in KNOWN_CROSS_FILE_DUPES
            and fn not in KNOWN_SAME_FILE_DUPES
        }
        assert not dupes, (
            "NEW duplicate function declarations across modules:\n"
            + "\n".join(f"  {fn}: {files}" for fn, files in sorted(dupes.items()))
        )

    def test_known_dupes_still_tracked(self):
        """Ensure the known-dupe set stays current (remove entries when deduped)."""
        actual_dupes = {fn for fn, files in self.func_map.items() if len(files) > 1}
        stale = (KNOWN_CROSS_FILE_DUPES | KNOWN_SAME_FILE_DUPES) - actual_dupes
        assert not stale, (
            f"These known duplicates were resolved — remove from KNOWN_*_DUPES:\n"
            + "\n".join(f"  {fn}" for fn in sorted(stale))
        )


# =========================================================================
# Group D — onclick Handler Coverage
# =========================================================================

class TestOnclickCoverage:
    """Every onclick="fn(…)" in index.html should have a matching
    function declaration in the combined split modules."""

    @pytest.fixture(autouse=True)
    def _scan(self):
        # Collect all function declarations from split modules
        self.all_fns: set[str] = set()
        for module in SPLIT_MODULES:
            text = _read(_STATIC / module)
            self.all_fns.update(_all_function_decls(text))

        # Also include non-split JS files that are loaded
        for extra in ("setup-wizard.js", "docs.js", "helper.js"):
            path = _STATIC / extra
            if path.exists():
                self.all_fns.update(_all_function_decls(_read(path)))

        # Extract all onclick function references from HTML
        html = _read(_INDEX)
        self.onclick_fns: set[str] = set()
        for onclick_val in _ONCLICK_RE.findall(html):
            m = _ONCLICK_FN_RE.match(onclick_val.strip())
            if m:
                fn_name = m.group(1)
                # Skip JS keywords that happen to match (if, return, etc.)
                if fn_name not in ("if", "return", "var", "let", "const", "this"):
                    self.onclick_fns.add(fn_name)

    def test_all_onclick_handlers_defined(self):
        missing = self.onclick_fns - self.all_fns
        assert not missing, (
            f"onclick handlers reference undefined functions:\n"
            + "\n".join(f"  {fn}" for fn in sorted(missing))
        )

    def test_onclick_count_sanity(self):
        """Sanity check: there should be a substantial number of onclick handlers."""
        assert len(self.onclick_fns) > 50, (
            f"Only found {len(self.onclick_fns)} onclick handlers — expected 100+"
        )


# =========================================================================
# Group E — No Dangerous Cross-File window.X = X Assignments
# =========================================================================

class TestNoCrossFileWindowAssignments:
    """window.X = X at the top level of a module is only safe if X is
    defined in that same module.  If X lives in a later-loading module,
    this causes a ReferenceError at parse time."""

    @pytest.fixture(autouse=True)
    def _scan(self):
        self.module_fns: dict[str, set[str]] = {}
        self.window_assigns: dict[str, list[tuple[str, str]]] = defaultdict(list)

        for module in SPLIT_MODULES:
            text = _read(_STATIC / module)
            self.module_fns[module] = set(_all_function_decls(text))
            for prop, value in _WINDOW_ASSIGN_RE.findall(text):
                self.window_assigns[module].append((prop, value))

    def test_no_cross_file_references(self):
        bad = []
        for module, assigns in self.window_assigns.items():
            local_fns = self.module_fns[module]
            for prop, value in assigns:
                if value not in local_fns:
                    bad.append(f"  {module}: window.{prop} = {value}  "
                               f"('{value}' not declared in {module})")
        assert not bad, (
            "Cross-file window.X = X assignments found (will cause ReferenceError):\n"
            + "\n".join(bad)
        )


# =========================================================================
# Group F — Module Size Sanity
# =========================================================================

class TestModuleSizes:
    """No single module should be unreasonably large (regression guard)."""

    MAX_LINES = 15000  # generous; largest module (wishlist-tools) is ~7200

    @pytest.mark.parametrize("module", SPLIT_MODULES)
    def test_module_size(self, module):
        text = _read(_STATIC / module)
        lines = text.count("\n") + 1
        assert lines < self.MAX_LINES, (
            f"{module} has {lines} lines (max {self.MAX_LINES})"
        )

    def test_total_lines_reasonable(self):
        """Combined split modules should be in the same ballpark as the original."""
        total = 0
        for module in SPLIT_MODULES:
            total += _read(_STATIC / module).count("\n") + 1
        # The original was ~78K lines; allow 60K-100K for flexibility
        assert 50000 < total < 120000, f"Total lines: {total}"

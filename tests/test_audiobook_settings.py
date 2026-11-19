"""Guards for the audiobook settings surface.

Two things are being protected here.

The first is that audiobooks have their OWN download source chain. Music's chain
lists tidal, qobuz, hifi, deezer and amazon — music-streaming services with no
audiobooks in them at all — so a book searched down the music chain burns an
attempt on each of them before it can succeed. The download ENGINE is shared on
purpose (an audiobook is structurally an album: a directory of ordered chapter
files with shared metadata, which is what album_bundle already handles); only the
ordering of sources differs.

The second is that a setting nobody wired up is a setting that does not exist.
Every new field is checked all the way through: the default, the input in
index.html, the code that loads it, and the code that saves it.
"""

from pathlib import Path

import pytest

from core.settings import ConfigManager

_ROOT = Path(__file__).resolve().parents[1]

# Sources that cannot serve an audiobook. Adding one of these to the audiobook
# chain means every book search spends an attempt on a music-only service.
_MUSIC_ONLY_SOURCES = ("tidal", "qobuz", "hifi", "deezer", "amazon", "lidarr", "bandcamp")


@pytest.fixture(scope="module")
def defaults():
    """The default config template, read without touching the real database."""
    return ConfigManager.__new__(ConfigManager)._get_default_config()


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def test_audiobooks_have_their_own_library_path(defaults):
    assert defaults["library"]["audiobooks_path"]


def test_audiobook_path_is_not_the_music_or_podcast_path(defaults):
    # Chapter files under a music root get indexed as albums by every media
    # server there is.
    library = defaults["library"]
    assert library["audiobooks_path"] != library["podcasts_path"]
    assert library["audiobooks_path"] not in (library["music_paths"] or [])


def test_audiobook_organization_template_exists(defaults):
    template = defaults["file_organization"]["templates"]["audiobook_path"]
    assert "$author" in template
    assert "$title" in template


def test_audiobook_block_defaults(defaults):
    audiobooks = defaults["audiobooks"]
    assert audiobooks["download_path"]
    assert audiobooks["embed_metadata"] is True
    assert audiobooks["renumber_chapters"] is True
    # Existing behaviour unless the user opts in.
    assert audiobooks["remove_owned_from_wishlist"] is False


def test_audiobooks_do_not_inherit_the_music_source_chain(defaults):
    # The whole reason this block exists.
    music_mode = defaults["download_source"]["mode"]
    audiobook_source = defaults["audiobooks"]["download_source"]
    assert "mode" in audiobook_source
    assert audiobook_source is not defaults["download_source"]
    assert music_mode not in _MUSIC_ONLY_SOURCES or audiobook_source["mode"] != music_mode


@pytest.mark.parametrize("source", _MUSIC_ONLY_SOURCES)
def test_the_audiobook_chain_lists_no_music_only_service(defaults, source):
    chain = defaults["audiobooks"]["download_source"]
    assert chain["mode"] != source
    assert source not in (chain.get("hybrid_order") or [])


def test_the_audiobook_chain_is_made_of_real_sources(defaults):
    chain = defaults["audiobooks"]["download_source"]
    allowed = {"soulseek", "torrent", "usenet", "hybrid"}
    assert chain["mode"] in allowed
    for source in chain.get("hybrid_order") or []:
        assert source in allowed


def test_audiobooks_search_the_audiobook_indexer_category(defaults):
    # 3030 is Newznab's audiobook category. core/prowlarr_client.py already
    # defines it and deliberately keeps it out of music searches, so asking for
    # it here costs the music side nothing.
    from core.prowlarr_client import MUSIC_CATEGORY_AUDIOBOOK

    assert defaults["audiobooks"]["prowlarr_categories"] == [MUSIC_CATEGORY_AUDIOBOOK]


def test_the_podcast_settings_are_untouched(defaults):
    # Audiobooks were added beside podcasts; nothing about them should have moved.
    podcasts = defaults["podcasts"]
    assert podcasts["download_path"]
    assert podcasts["media_format"] == "audio"
    assert defaults["file_organization"]["templates"]["podcast_path"]


# ---------------------------------------------------------------------------
# Wiring — a setting nobody reads or writes is not a setting
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def index_html():
    return (_ROOT / "webui/index.html").read_text(encoding="utf-8", errors="ignore")


@pytest.fixture(scope="module")
def settings_js():
    return (_ROOT / "webui/src/features/settings/shell.ts").read_text(encoding="utf-8", errors="ignore")


_TABS = _ROOT / "webui/src/features/settings/tabs"


_QUALITY_VIEW = _TABS / "quality" / "audiobooks-view.tsx"


@pytest.fixture(scope="module")
def panel():
    """the Library tab's two Audiobooks panels, React since SETTINGS_PORT.md
    step 8c: what index.html and settings.js held for them; and Quality >
    Audiobooks, where Audiobook Quality moved in the settings-bic rebuild"""
    return (_TABS / "library-audiobooks.tsx").read_text(encoding="utf-8") + (
        _QUALITY_VIEW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def model():
    """the download chains, the audiobook one included (step 8c)"""
    return (_TABS / "sources-model.ts").read_text(encoding="utf-8")


def _has_input(src: str, element_id: str) -> bool:
    # a plain JSX attribute, or the id handed to the panel's field helpers
    return f'id="{element_id}"' in src or f"'{element_id}'," in src


@pytest.mark.parametrize("element_id", ["audiobooks-path", "template-audiobook-path",
                                        "audiobook-remove-owned-from-wishlist"])
def test_the_settings_page_has_the_input(panel, element_id):
    assert _has_input(panel, element_id)


def test_the_folder_path_input_can_be_unlocked(panel):
    # Every other output path has an unlock button; the React one is the
    # shared LockedPath, read-only until Unlock
    import re
    assert re.search(r'<LockedPath\s+(?:\w+=\{[^{}]*\}\s+)*id="audiobooks-path"', panel)


def _saves(panel: str, key: str) -> bool:
    """the panel saves this audiobooks.* key (a dotted key is one key of a
    dict inside the section, saved alone)"""
    import re
    return bool(re.search(rf"set\(\s*'{re.escape(key)}'", panel)) or f"{key}: value" in panel


def _reads(panel: str, key: str) -> bool:
    """the panel shows this audiobooks.* key"""
    if key.startswith("download_source."):
        return f"source.{key.split('.', 1)[1]}" in panel
    if key.startswith("quality."):
        return key in panel
    return f"ab.{key}" in panel


def test_the_three_fields_are_read_and_saved(panel):
    assert "store.set('audiobooks', 'download_path'" in panel
    assert "store.set('library', 'audiobooks_path'" in panel
    assert "ab.remove_owned_from_wishlist === true" in panel
    assert "set('remove_owned_from_wishlist'" in panel
    # the template is the Organization card's, which owns every template
    card = (_TABS / "library-organization.tsx").read_text(encoding="utf-8")
    assert "template={template('audiobook_path')}" in card
    assert "setOrg(`templates.${key}`" in card


def test_settings_js_no_longer_saves_the_audiobook_fields(settings_js):
    # React saves them (step 8c); a second saver would race it
    payload = settings_js.split("async function saveSettings", 1)[1]
    assert "audiobooks_path:" not in payload
    assert "templates.audiobook_path" not in payload
    assert "\n        audiobooks: {" not in payload


def test_the_template_reset_covers_audiobooks():
    # "Reset to defaults" that skips a field leaves a stale template behind.
    rules = (_TABS / "organization.ts").read_text(encoding="utf-8")
    reset = rules.split("export const RESET_TEMPLATES", 1)[1].split("} as const", 1)[0]
    assert "audiobook_path: '$author/$series/$seriespos - $title'" in reset


# ---------------------------------------------------------------------------
# Acquisition knobs
# ---------------------------------------------------------------------------

def test_downloads_get_their_own_client_category(defaults):
    # The grab code reads these; without defaults they fall back to a literal
    # and are neither discoverable nor settable.
    audiobooks = defaults["audiobooks"]
    assert audiobooks["torrent_category"]
    assert audiobooks["usenet_category"]


def test_the_downloader_category_is_not_the_music_one(defaults):
    # A finished book must never be mistaken for a music release by anything
    # watching the music category.
    audiobooks = defaults["audiobooks"]
    music_category = defaults.get("torrent_client", {}).get("category", "soulsync")
    assert audiobooks["torrent_category"] != music_category


def test_the_completeness_gate_is_on_by_default(defaults):
    # A partial book plays perfectly until the listener runs out of it, so this
    # must not be something you have to opt into.
    tolerance = defaults["audiobooks"]["completeness_tolerance"]
    assert 0.5 < tolerance <= 1.0


def test_a_short_book_is_staged_not_dropped(defaults):
    # Torrents finish late and uploaders repair releases.
    assert defaults["audiobooks"]["staging_days"] > 0


# ---------------------------------------------------------------------------
# The rest of the knobs, on the page
# ---------------------------------------------------------------------------

# Every audiobook key that is meant to be editable, paired with its input.
# A key missing from here is a key nobody can change without editing the
# database by hand, which is how the first ten of these spent a week invisible.
_EXPOSED = {
    "marketplace": "audiobook-marketplace",
    "download_source.mode": "audiobook-download-mode",
    "quality.format_order": "audiobook-format-first",
    "quality.min_bitrate_kbps": "audiobook-min-bitrate",
    "quality.max_bitrate_kbps": "audiobook-max-bitrate",
    "quality.allow_dramatized": "audiobook-allow-dramatized",
    "quality.allowed_formats": "audiobook-allowed-formats",
    "quality.file_layout": "audiobook-file-layout",
    "recycle_deletes": "audiobook-recycle-deletes",
    "recycle_keep_days": "audiobook-recycle-keep-days",
    "torrent_category": "audiobook-torrent-category",
    "prowlarr_categories": "audiobook-prowlarr-categories",
    "completeness_tolerance": "audiobook-completeness-tolerance",
    "staging_days": "audiobook-staging-days",
    "renumber_chapters": "audiobook-renumber-chapters",
    "embed_metadata": "audiobook-embed-metadata",
    "embed_artwork": "audiobook-embed-artwork",
    "save_artwork": "audiobook-save-artwork",
    "write_nfo": "audiobook-write-nfo",
}


@pytest.mark.parametrize("element_id", sorted(set(_EXPOSED.values())))
def test_every_audiobook_knob_has_an_input(panel, element_id):
    assert _has_input(panel, element_id)


@pytest.mark.parametrize("key", sorted(_EXPOSED))
def test_every_audiobook_knob_is_loaded_and_saved(panel, key):
    assert _reads(panel, key), f"audiobooks.{key} is never shown"
    assert _saves(panel, key), f"audiobooks.{key} is never saved"


@pytest.mark.parametrize("key", sorted(_EXPOSED))
def test_every_exposed_knob_has_a_default(defaults, key):
    block = defaults["audiobooks"]
    for part in key.split("."):
        assert part in block, f"audiobooks.{key} has no default"
        block = block[part]


def test_every_audiobook_knob_explains_itself():
    # every help body of the old panel is in the React one, word for word:
    # library-audiobooks.test.tsx compares them against the frozen markup
    test = (_TABS / "library-audiobooks.test.tsx").read_text(encoding="utf-8")
    assert "library-audiobook-organization.vanilla.html" in test
    assert "'.setting-help-body, .stg-help-text, .settings-hint, label'" in test
    fixture = (_TABS / "__fixtures__/library-audiobook-organization.vanilla.html").read_text(
        encoding="utf-8")
    # the quality knobs moved to Quality > Audiobooks: each is a Row there
    # with its one line of help and the rest behind the "i"
    view = _QUALITY_VIEW.read_text(encoding="utf-8")
    for key, element_id in _EXPOSED.items():
        if key.startswith("quality."):
            at = view.index(f'id="{element_id}"')
            row = view[view.rindex("<Row", 0, at):at]
            assert "help=" in row and "more=" in row, f"{element_id} has no help text"
            continue
        tail = fixture.split(f'id="{element_id}"', 1)[1][:2000]
        assert "setting-help-body" in tail, f"{element_id} has no help text"


def test_the_audiobook_block_actually_persists():
    """``audiobooks`` must be in web_server's save whitelist.

    The POST handler walks a hard-coded list of section names and writes each
    one key by key. A section missing from that list is accepted, answered with
    "Settings saved successfully", and silently discarded — which is exactly
    what happened to the audiobook block for its first week.
    """
    source = (_ROOT / "web_server.py").read_text(encoding="utf-8", errors="ignore")
    line = next(ln for ln in source.splitlines() if "for service in [" in ln)
    assert "'audiobooks'" in line


def test_saving_an_empty_category_list_cannot_disable_searching():
    # The field is free text, so it can be cleared. Falling through to
    # Prowlarr's default would search the music tree and return albums.
    from unittest.mock import MagicMock, patch

    from core.audiobook_release_search import AUDIOBOOK_CATEGORY, _configured_categories

    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: []
    with patch("core.settings.config_manager", manager):
        assert _configured_categories() == [AUDIOBOOK_CATEGORY]


def test_saving_an_empty_source_order_cannot_disable_downloading():
    from unittest.mock import MagicMock, patch

    from core.audiobook_release_search import configured_chain

    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: (
        "hybrid" if key.endswith(".mode") else []
    )
    with patch("core.settings.config_manager", manager):
        assert configured_chain() == ["torrent", "usenet", "soulseek"]


def test_the_completeness_field_is_a_percentage_of_the_stored_fraction(panel):
    assert "audiobook-completeness-tolerance" in panel
    assert "* 100" in panel and "/ 100" in panel


def test_the_two_downloader_categories_stay_in_step(panel):
    assert "store.patch('audiobooks', { torrent_category: value, usenet_category: value })" in panel


def test_the_audiobook_group_is_labelled_on_the_page(panel):
    # these knobs used to sit in the music section under a heading, and the
    # next reader took them for global settings. they have their own tab now.
    organization = panel.split("export function AudiobookOrganizationPanel", 1)[1]
    assert 'id="organization-audiobooks-panel"' in organization
    for knob in ('template-audiobook-path', 'audiobook-marketplace', 'audiobook-write-nfo'):
        assert _has_input(organization, knob)


@pytest.mark.parametrize("css_class", ["settings-subheading", "settings-unit"])
def test_the_new_markup_is_actually_styled(css_class):
    # A class that exists only in the HTML renders as unstyled text, which
    # looks broken rather than new.
    css = (_ROOT / "webui/static/style.css").read_text(encoding="utf-8", errors="ignore")
    assert f".{css_class}" in css


# ---------------------------------------------------------------------------
# The source chain, built the way music and video build theirs
# ---------------------------------------------------------------------------

def test_the_source_order_is_not_a_text_box(panel):
    """Ordering sources is a reorder, not a spelling test: the chain widget
    on the Downloads tab orders them (sources-model.ts)."""
    assert 'id="audiobook-hybrid-order"' not in panel
    assert 'id="audiobook-hybrid-rows"' in panel


def test_the_source_rows_reuse_the_shared_markup(panel):
    block = panel.split('id="audiobook-hybrid-container"', 1)[1][:800]
    assert 'className="hybrid-source-list"' in block


def test_the_source_rows_can_be_reordered_and_toggled(model):
    # the chain widget's audiobook tab moves, adds and removes them, and saves
    # through the store
    assert "else if (kind === 'audiobooks') writeAudiobooks(order);" in model
    assert "'download_source.hybrid_order': order.slice()" in model


def test_the_chain_is_only_shown_when_it_applies(panel):
    assert "display: loads && mode === 'hybrid' ? '' : 'none'" in panel


def test_the_last_source_cannot_be_switched_off():
    # the order list (settings-bic) holds the rule for all three orders: a
    # row can only go while more than the fewest allowed are left, by the
    # menu and by dragging it out
    widget = (_TABS.parent / "ui" / "order-list.tsx").read_text(encoding="utf-8")
    assert "minItems = 1," in widget
    assert "const canRemove = ids.length > minItems;" in widget
    body = widget.split("const remove = (id: string, by: 'menu' | 'drag') => {", 1)[1].split("};", 1)[0]
    assert "if (!canRemove) return;" in body
    assert "live.current.canRemove && middle > poolBox.top" in widget


def test_the_order_is_loaded_and_saved(model):
    assert "source.hybrid_order" in model.split("export function audiobookChainOf", 1)[1][:600]
    assert "'download_source.hybrid_order': order.slice()" in model


def test_an_unknown_source_cannot_be_saved(model):
    assert model.count("AUDIOBOOK_SOURCES.includes(") >= 2


def test_the_offered_sources_match_the_backend_chain(defaults, model):
    line = next(ln for ln in model.splitlines()
                if ln.startswith("export const AUDIOBOOK_SOURCES"))
    offered = {part.strip(" '\"") for part in
               line.split("[", 1)[1].split("]", 1)[0].split(",")}
    assert offered == set(defaults["audiobooks"]["download_source"]["hybrid_order"])


def test_the_mode_options_match_the_backend(defaults, panel):
    block = panel.split('id="audiobook-download-mode"', 1)[1].split("</select>", 1)[0]
    offered = {part.split('"')[0] for part in block.split('value="')[1:]}
    assert offered == {"soulseek", "torrent", "usenet", "hybrid"}


# ---------------------------------------------------------------------------
# The automations page
# ---------------------------------------------------------------------------

_AUDIOBOOK_ACTIONS = (
    "audiobook_process_wishlist",
    "audiobook_scan_watchlist",
    "audiobook_scan_library",
)


@pytest.fixture(scope="module")
def automations_js():
    return (_ROOT / "webui/static/stats-automations.js").read_text(
        encoding="utf-8", errors="ignore")


@pytest.mark.parametrize("action", _AUDIOBOOK_ACTIONS)
def test_every_audiobook_automation_has_a_readable_name(automations_js, action):
    """The page renders an action by looking it up in a label map.

    An action missing from it falls back to its raw name, so a user sees
    "audiobook_scan_library" sitting next to "Scan Video Library". The video
    side registers all twenty-odd of its actions; audiobooks registered none.
    """
    assert f"{action}: '" in automations_js


@pytest.mark.parametrize("action", _AUDIOBOOK_ACTIONS)
def test_every_audiobook_automation_has_an_icon(automations_js, action):
    icons = automations_js.split("const _autoIcons = {", 1)[1].split("};", 1)[0]
    assert f"{action}: '" in icons


@pytest.mark.parametrize("action", _AUDIOBOOK_ACTIONS)
def test_every_audiobook_automation_is_actually_registered(action):
    # A label for an action nobody registered is a row that can never run.
    registration = (_ROOT / "core/automation/handlers/registration.py").read_text(
        encoding="utf-8", errors="ignore")
    assert f"'{action}'" in registration


def test_audiobook_automations_sit_on_the_music_page(defaults):
    """No owned_by, matching the podcast scan.

    owned_by='video' is what keeps the video jobs OFF the music automations
    page. Audio-side jobs belong with music, so they must not carry one.
    """
    from core.automation_engine import SYSTEM_AUTOMATIONS

    for entry in SYSTEM_AUTOMATIONS:
        if str(entry.get("action_type", "")).startswith("audiobook_"):
            assert "owned_by" not in entry, entry["name"]


# ---------------------------------------------------------------------------
# The watchlist button is the app-wide one
# ---------------------------------------------------------------------------

def _read(rel):
    return (_ROOT / rel).read_text(encoding="utf-8", errors="ignore")


def test_the_author_page_uses_the_standard_watchlist_button():
    """Same button as artist-detail, label-detail and podcasts.

    It was a bespoke "Follow for new releases" pill. Watching a thing is one
    concept across the app and it has one control: the eye, the global
    library-artist-watchlist-btn class, and the same Add to Watchlist /
    Watching wording.
    """
    page = _read("webui/src/routes/audiobooks/-ui/audiobook-person-page.tsx")
    assert "library-artist-watchlist-btn" in page
    assert "watchlist-icon" in page and "\U0001f441" in page
    assert "Add to Watchlist" in page
    assert "Follow for new releases" not in page


def test_the_author_page_button_has_no_bespoke_styling_left():
    css = _read("webui/src/routes/audiobooks/-ui/audiobooks-page.module.css")
    assert "followBtn" not in css


def test_author_cards_carry_the_standard_watch_badge():
    # Same classes and the same top-right container the library artist cards
    # use, so the badge lands in the same place and behaves the same way.
    row = _read("webui/src/routes/audiobooks/-ui/audiobook-people-row.tsx")
    for token in ("card-badge-container", "watch-card-icon", "source-card-icon",
                  "watch-icon-emoji", "watch-icon-label"):
        assert token in row, token


def test_the_card_badge_does_not_navigate():
    # The tile is a link. Without swallowing the click, watching an author
    # would open their page instead.
    row = _read("webui/src/routes/audiobooks/-ui/audiobook-people-row.tsx")
    assert "preventDefault" in row and "stopPropagation" in row


def test_only_authors_can_be_watched_from_a_card():
    # A narrator has no release of their own; they appear on someone else's.
    row = _read("webui/src/routes/audiobooks/-ui/audiobook-people-row.tsx")
    assert "person.role === 'author' &&" in row


def test_the_card_badge_expands_on_hover_like_every_other_card():
    # style.css scopes the expand to .library-artist-card, so the tile has to
    # restate it or the label would never appear.
    css = _read("webui/src/routes/audiobooks/-ui/audiobooks-page.module.css")
    assert ":global(.watch-card-icon)" in css
    assert ":global(.watch-icon-label)" in css


# ---------------------------------------------------------------------------
# Helping the reader choose a release
# ---------------------------------------------------------------------------

def test_the_release_list_names_its_top_pick():
    """The ranking had an opinion and was keeping it to itself.

    "Not sure what the best choice is" is a fair complaint about a list that
    is already sorted by narrator match, format and size plausibility without
    ever saying so.
    """
    modal = _read("webui/src/routes/audiobooks/-ui/audiobook-releases-modal.tsx")
    assert "Best from" in modal
    assert "styles.bestMatch" in modal


def test_the_top_pick_is_named_per_source_not_just_overall():
    """Marking only the overall winner meant nothing could be labelled until
    the whole search settled, because the next indexer to answer could take
    the crown. Per source it is decidable as soon as that source replies.
    """
    modal = _read("webui/src/routes/audiobooks/-ui/audiobook-releases-modal.tsx")
    assert "Best from" in modal
    assert "bestKeys" in modal
    # No longer gated on the search having finished.
    assert "!loading" not in modal.split("const best =", 1)[1].split("\n", 1)[0]


def test_the_best_of_each_source_is_listed_first():
    # Twenty rows from one indexer used to bury a better hit from another.
    modal = _read("webui/src/routes/audiobooks/-ui/audiobook-releases-modal.tsx")
    assert "ordered: [...bests, ...rest]" in modal
    assert "ordered.map(" in modal


def test_the_remainder_groups_by_type():
    modal = _read("webui/src/routes/audiobooks/-ui/audiobook-releases-modal.tsx")
    block = modal.split("rest.sort(", 1)[1].split("\n", 1)[0]
    assert "protocol" in block



def test_the_size_of_a_release_is_explained_not_just_shown():
    # A name and a size cannot be judged: 800MB is generous for a six-hour
    # book and thin for a forty-hour one.
    modal = _read("webui/src/routes/audiobooks/-ui/audiobook-releases-modal.tsx")
    assert "implied_kbps" in modal
    assert "quality_note" in modal


def test_an_abridged_release_says_its_bitrate_is_rough():
    # Its real runtime is shorter than the catalogue's, so the arithmetic
    # reads high. Quietly misleading would be worse than not showing it.
    modal = _read("webui/src/routes/audiobooks/-ui/audiobook-releases-modal.tsx")
    block = modal.split("release.implied_kbps ?", 1)[1][:900]
    assert "abridged" in block


def test_soulseek_groups_as_one_source_not_one_per_peer():
    """A peer offers one folder for a book.

    Grouping by peer made every Soulseek row its own group and therefore its
    own "best of", and a label on everything is a label on nothing. Soulseek
    is one source with many peers, like an indexer is one source with many
    uploads.
    """
    modal = _read("webui/src/routes/audiobooks/-ui/audiobook-releases-modal.tsx")
    block = modal.split("const sourceOf =", 1)[1].split(";", 1)[0]
    assert "'Soulseek'" in block
    assert "soulseek?.username" not in block


def test_the_peer_is_still_named_on_the_row():
    # Grouping under one heading must not lose who is actually serving it.
    modal = _read("webui/src/routes/audiobooks/-ui/audiobook-releases-modal.tsx")
    assert "release.soulseek.username" in modal


def test_a_grabbed_row_shows_its_own_download_status():
    """Pointing at another page is how "stuck" gets reported.

    A book can sit held for days for a good reason — the release turned out
    to be part 1 of 5 — and without the reason on the row a held book is
    indistinguishable from a hung one.
    """
    modal = _read("webui/src/routes/audiobooks/-ui/audiobook-releases-modal.tsx")
    assert "grabbedRefs" in modal
    assert "tracked.completeness" in modal
    assert "STATUS_WORDS" in modal


def test_the_status_words_are_plain_english():
    # "staged" means nothing to anyone reading it.
    modal = _read("webui/src/routes/audiobooks/-ui/audiobook-releases-modal.tsx")
    block = modal.split("const STATUS_WORDS", 1)[1].split("};", 1)[0]
    assert "'Held back'" in block
    assert "'In your library'" in block


def test_the_grab_keeps_the_reference_it_is_given():
    # Without the ref the row has nothing to follow.
    api = _read("webui/src/routes/audiobooks/-audiobooks.api.ts")
    block = api.split("export async function grabRelease", 1)[1].split("\n}", 1)[0]
    assert "ref: data?.ref" in block


def test_polling_stops_once_everything_settles():
    # A modal left open must not poll for the life of the tab.
    modal = _read("webui/src/routes/audiobooks/-ui/audiobook-releases-modal.tsx")
    assert "const settled = refs.every(" in modal
    assert "if (!settled)" in modal


def test_the_quality_profile_is_one_for_the_whole_side():
    """Not one per followed author.

    A listener's idea of an acceptable file does not change between authors,
    and the per-author card already carries the two settings that genuinely do
    differ there — whether to auto-queue, and which narrator.
    """
    modal = _read("webui/src/routes/watchlist/-ui/audiobook-author-settings-modal.tsx")
    for absent in ("format_order", "min_bitrate", "max_bitrate", "allow_dramatized"):
        assert absent not in modal, absent


def test_the_preferred_format_choice_keeps_the_others_behind_it():
    # Picking MP3 must not silently discard every other format.
    # Quality > Audiobooks has it since the settings-bic rebuild
    panel = _read("webui/src/features/settings/tabs/quality/audiobooks-view.tsx")
    block = panel.split("set('quality.format_order', [", 1)[1].split("]);", 1)[0]
    assert "first," in block
    assert ".filter((f) => f !== first)" in block


def test_the_quality_floor_is_separate_from_the_completeness_floor():
    # They answer different questions: could this be the whole book at all,
    # versus do I want it. Sharing one number would conflate fact with taste.
    panel = _read("webui/src/features/settings/tabs/library-audiobooks.tsx")
    quality = _read("webui/src/features/settings/tabs/quality/audiobooks-view.tsx")
    # two fields, on two pages: Quality > Audiobooks and Library
    assert 'id="audiobook-min-bitrate"' in quality
    assert "'audiobook-completeness-tolerance'," in panel
    assert "min_complete_kbps" not in panel + quality

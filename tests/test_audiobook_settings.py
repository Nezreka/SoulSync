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
    return (_ROOT / "webui/static/settings.js").read_text(encoding="utf-8", errors="ignore")


@pytest.mark.parametrize("element_id", ["audiobooks-path", "template-audiobook-path"])
def test_the_settings_page_has_the_input(index_html, element_id):
    assert f'id="{element_id}"' in index_html


def test_the_folder_path_input_can_be_unlocked(index_html, settings_js):
    # Every other output path has an unlock button plus an entry in the path map;
    # without the map entry the button silently does nothing.
    assert "togglePathLock('audiobooks'" in index_html
    assert "audiobooks: 'audiobooks-path'" in settings_js


def _touch_count(js: str, element_id: str) -> int:
    """How many places in settings.js reach this field.

    The save side reads through _cfgStr/_cfgBool/_cfgInt/_cfgFloat rather than
    getElementById now — those return undefined for a missing element so a field
    that is not on the page can no longer blank the stored setting. Counting
    only getElementById would report every saved field as write-only.
    """
    return (js.count(f"getElementById('{element_id}')")
            + sum(js.count(f"_cfg{kind}('{element_id}'")
                  for kind in ("Str", "Bool", "Int", "Float")))


@pytest.mark.parametrize("element_id", ["audiobooks-path", "template-audiobook-path"])
def test_settings_js_reads_and_writes_the_input(settings_js, element_id):
    assert _touch_count(settings_js, element_id) >= 2


def test_settings_js_persists_the_audiobook_fields(settings_js):
    assert "audiobooks_path:" in settings_js
    assert "audiobook_path:" in settings_js


def test_the_template_reset_covers_audiobooks(settings_js):
    # "Reset to defaults" that skips a field leaves a stale template behind.
    assert "defaults.audiobook" in settings_js


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
    "download_source.mode": "audiobook-download-mode",
    "quality.format_order": "audiobook-format-first",
    "quality.min_bitrate_kbps": "audiobook-min-bitrate",
    "quality.max_bitrate_kbps": "audiobook-max-bitrate",
    "quality.allow_dramatized": "audiobook-allow-dramatized",
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
def test_every_audiobook_knob_has_an_input(index_html, element_id):
    assert f'id="{element_id}"' in index_html


@pytest.mark.parametrize("element_id", sorted(set(_EXPOSED.values())))
def test_every_audiobook_knob_is_loaded_and_saved(settings_js, element_id):
    # Twice: once populating the form, once collecting it. One occurrence means
    # a field that either shows the wrong value or throws its away on save.
    assert _touch_count(settings_js, element_id) >= 2


@pytest.mark.parametrize("key", sorted(_EXPOSED))
def test_every_exposed_knob_has_a_default(defaults, key):
    block = defaults["audiobooks"]
    for part in key.split("."):
        assert part in block, f"audiobooks.{key} has no default"
        block = block[part]


@pytest.mark.parametrize("element_id", sorted(set(_EXPOSED.values())))
def test_every_audiobook_knob_explains_itself(index_html, element_id):
    # Every other field on this page carries a help body. One that does not is
    # a number the user has no way to reason about.
    tail = index_html.split(f'id="{element_id}"', 1)[1][:2000]
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


def test_the_completeness_field_is_a_percentage_of_the_stored_fraction(settings_js):
    # Stored as 0.92 because that is what the gate multiplies by; shown as 92
    # because "0.92" in a box labelled % is a bug report waiting to happen.
    assert "audiobook-completeness-tolerance" in settings_js
    assert "* 100" in settings_js and "/ 100" in settings_js


def test_the_two_downloader_categories_stay_in_step(settings_js):
    # One field fills both. Two boxes asking for the same word twice is two
    # chances to get it wrong, and a mismatch means usenet books land in a
    # category nothing is watching.
    # Brace-matched rather than split on "}," — the block has a nested object
    # in it, and a naive split stops at the wrong place.
    start = settings_js.index("audiobooks: {") + len("audiobooks: ")
    depth = 0
    for end, char in enumerate(settings_js[start:], start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                break
    block = settings_js[start:end + 1]

    assert block.count("audiobook-torrent-category") == 2
    assert "usenet_category:" in block


def test_the_audiobook_group_is_labelled_on_the_page(index_html):
    # These knobs sit inside a section that is otherwise about music paths.
    # Without a heading the next reader takes them for global settings.
    assert 'class="settings-subheading">Audiobooks<' in index_html


@pytest.mark.parametrize("css_class", ["settings-subheading", "settings-unit"])
def test_the_new_markup_is_actually_styled(css_class):
    # A class that exists only in the HTML renders as unstyled text, which
    # looks broken rather than new.
    css = (_ROOT / "webui/static/style.css").read_text(encoding="utf-8", errors="ignore")
    assert f".{css_class}" in css


# ---------------------------------------------------------------------------
# The source chain, built the way music and video build theirs
# ---------------------------------------------------------------------------

def test_the_source_order_is_not_a_text_box(index_html):
    """Ordering sources is a reorder, not a spelling test.

    A comma-separated field makes the user remember three exact source names,
    lets them type a fourth that silently does nothing, and shows no sign that
    the order even matters. Music and video both solved this with draggable /
    arrow-ordered rows; there is no reason for a third dialect.
    """
    assert 'id="audiobook-hybrid-order"' not in index_html
    assert 'id="audiobook-hybrid-rows"' in index_html


def test_the_source_rows_reuse_the_shared_markup(index_html):
    # Same class as music's and video's chains, so all three inherit one
    # stylesheet and one set of behaviours.
    block = index_html.split('id="audiobook-hybrid-container"', 1)[1][:800]
    assert 'class="hybrid-source-list"' in block


def test_the_source_rows_can_be_reordered_and_toggled(settings_js):
    assert "function moveAudiobookSource" in settings_js
    assert "function toggleAudiobookSource" in settings_js
    assert "moveAudiobookSource(" in settings_js
    assert "toggleAudiobookSource(" in settings_js


def test_the_chain_is_only_shown_when_it_applies(index_html, settings_js):
    # A single-source mode has nothing to order, so the rows would imply a
    # choice that does nothing.
    assert "onAudiobookModeChange()" in index_html
    assert "function onAudiobookModeChange" in settings_js


def test_the_last_source_cannot_be_switched_off(settings_js):
    # An empty chain means no book can ever be found, with nothing on screen
    # saying why. Video guards this the same way.
    body = settings_js.split("function toggleAudiobookSource", 1)[1].split("\n}", 1)[0]
    assert "_audiobookHybrid.length <= 1" in body


def test_the_order_is_loaded_and_saved(settings_js):
    assert "_audiobookHybrid = (abSource.hybrid_order" in settings_js
    assert "hybrid_order: _audiobookHybrid" in settings_js


def test_an_unknown_source_cannot_be_saved(settings_js):
    # The old text box accepted anything typed into it.
    assert settings_js.count("AUDIOBOOK_SOURCES.includes(") >= 2


def test_the_offered_sources_match_the_backend_chain(defaults, settings_js):
    # The rows are built from a hard-coded list. If it ever drifts from the
    # default chain, a source becomes unreachable from the page.
    line = next(ln for ln in settings_js.splitlines()
                if ln.startswith("const AUDIOBOOK_SOURCES"))
    offered = {part.strip(" '\"") for part in
               line.split("[", 1)[1].split("]", 1)[0].split(",")}
    assert offered == set(defaults["audiobooks"]["download_source"]["hybrid_order"])


def test_the_mode_options_match_the_backend(defaults, index_html):
    # A mode the backend does not understand falls through to the default
    # chain, silently ignoring what the user picked.
    block = index_html.split('id="audiobook-download-mode"', 1)[1].split("</select>", 1)[0]
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
    js = _read("webui/static/settings.js")
    block = js.split("format_order: (function", 1)[1].split("})()", 1)[0]
    assert "filter(f => f !== first)" in block
    assert "[first, ...rest]" in block


def test_the_quality_floor_is_separate_from_the_completeness_floor():
    # They answer different questions: could this be the whole book at all,
    # versus do I want it. Sharing one number would conflate fact with taste.
    index = _read("webui/index.html")
    assert 'id="audiobook-min-bitrate"' in index
    assert 'id="audiobook-completeness-tolerance"' in index
    assert "min_complete_kbps" not in index

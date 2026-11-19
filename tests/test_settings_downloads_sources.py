"""Downloads > Sources, as the settings-bic redesign built it.

The Sources tab and the Downloads tab's download chain became one pill:
the order list, the sources not used under it, the indexers, and each
source's settings in a side sheet. The tests that pinned the old chain
widget, the source tiles and the source modal (test_download_chain_widget.py,
test_settings_sources_tab.py) pinned markup that is gone; what they were FOR
is pinned here against the files that do the job now.

Behaviour is tested in the frontend (ui/order-list.test.tsx,
tabs/sources.test.tsx, tabs/downloads.test.tsx, the key guard). These read the
sources as text, like their predecessors, for the rules that are easy to lose
in a refactor.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SETTINGS = _ROOT / "webui" / "src" / "features" / "settings"


def _read(rel: str) -> str:
    return (_SETTINGS / rel).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def view():
    return _read("tabs/downloads/sources-view.tsx")


@pytest.fixture(scope="module")
def order():
    return _read("ui/order-list.tsx")


@pytest.fixture(scope="module")
def words():
    return _read("tabs/downloads/words.ts")


@pytest.fixture(scope="module")
def model():
    return _read("tabs/sources-model.ts")


@pytest.fixture(scope="module")
def nav():
    return _read("nav.ts")


@pytest.fixture(scope="module")
def page():
    return _read("tabs/downloads/downloads-page.tsx")


# ── where it lives ──

def test_sources_is_a_pill_of_downloads_and_the_old_tab_key_still_opens_it(nav):
    downloads = nav[nav.index("key: 'downloads',"):nav.index("key: 'quality',")]
    assert "{ key: 'sources', label: 'Sources' }," in downloads
    # switchSettingsTab('sources') from the sidebar, help and the video side
    assert "sources: { tab: 'downloads', view: 'sources' }," in nav
    # the first pill, so opening Downloads opens the order
    assert downloads.index("key: 'sources'") < downloads.index("key: 'behaviour'")


def test_it_is_shared_by_both_sides(view, nav):
    """Both sides download through these sources. A side marker on the view or
    on the pill would hide it from one of them."""
    assert "data-music-only" not in view and "data-video-only" not in view
    assert "{ key: 'sources', label: 'Sources' }," in nav  # no `side` on it
    # only Filtering is one side's
    assert "{ key: 'filtering', label: 'Filtering', side: 'music' }," in nav


def test_the_widget_has_somewhere_to_render_and_keeps_its_hook(view):
    # video-service-status.js and service-switch.ts scroll to this id
    assert '<div id="download-chain-widget" data-kind={kind}>' in view
    for reader in ("webui/static/video/video-service-status.js", "webui/src/shell/service-switch.ts"):
        assert "#download-chain-widget" in (_ROOT / reader).read_text(encoding="utf-8")


def test_the_three_orders_share_one_list(view, model):
    kinds = model.split("export const CHAIN_KINDS", 1)[1].split("\n};", 1)[0]
    assert re.findall(r"^  (\w+): \{$", kinds, re.M) == ["music", "video", "audiobooks"]
    assert view.count("<OrderList") == 1
    # the kind switch is a tablist over the one panel
    assert 'id="dlchain-tabs"' in view and 'panel="dlchain-panel"' in view
    assert "onChange={(next) => model.switchChain(next)}" in view


def test_the_video_order_is_re_read_on_arrival(view):
    """video.db can change from the video side while this page is open."""
    arrive = view.split("// arriving:", 1)[1].split("}, [opened, active]);", 1)[0]
    assert "void model.loadChain();" in arrive
    assert "void model.loadVideoChain();" in arrive
    assert "model.autoTestOnce();" in arrive


# ── the order ──

def test_a_source_is_on_by_being_in_the_order(view, order):
    """No enable switch per source: there is nothing on the server to switch."""
    assert "Switch" not in view.split("export function SourcesView", 1)[1]
    assert 'type="checkbox"' not in order


def test_the_order_cannot_be_emptied(order, view):
    assert "minItems = 1," in order
    assert "const canRemove = ids.length > minItems;" in order
    remove = order.split("const remove = (id: string, by: 'menu' | 'drag') => {", 1)[1].split("};", 1)[0]
    assert "if (!canRemove) return;" in remove
    # dragging the last one out is refused too
    assert "live.current.canRemove && middle > poolBox.top" in order
    # and it says why, where the person is looking
    assert "disabled={!canRemove}" in order
    assert "{canRemove ? null : <span className={s.menuKbd}>{keepText}</span>}" in order
    assert 'keepText="Keep at least one source"' in view


def test_a_row_says_where_it_sits_and_which_source_it_is(order, words):
    assert "<span className={s.rank} aria-hidden=\"true\">" in order
    assert "role(item.id, index, shown)" in order
    assert "<div className={s.srcName}>{item.name}</div>" in order
    assert "'Tried first'" in words and "finds nothing`" in words
    assert "`The only source for ${KIND_WORD[kind]}`" in words
    # audiobooks are fetched as a folder: the order is who is asked first
    assert "'Asked first'" in words


def test_the_logo_carries_the_row_and_nothing_is_an_emoji(words, view):
    look = words.split("export const SOURCE_LOOK", 1)[1].split("\n};", 1)[0]
    for source in ("soulseek", "youtube", "tidal", "qobuz", "hifi", "deezer_dl", "lidarr",
                   "soundcloud", "torrent", "usenet", "extto", "prowlarr"):
        assert re.search(rf"^  {source}: \{{", look, re.M), f"{source} has no look"
    # every one has a line icon for when there is no logo file
    assert look.count("icon: '") == 12
    # the dark marks are drawn white, in the order and under it alike
    for dark in ("tidal", "qobuz", "soundcloud"):
        block = look.split(f"  {dark}: {{", 1)[1].split("},", 1)[0]
        assert "invert: true," in block, f"{dark} would be black on black"
    assert not re.search(r"[\U0001F300-\U0001FAFF☀-➿]", view + look)


def test_the_name_reaches_a_pointer_and_a_reader(order):
    assert "aria-label={`Reorder ${item.name}, ${ids.indexOf(item.id) + 1} of ${ids.length}`}" in order
    assert "aria-label={`More for ${item.name}`}" in order
    assert "aria-label={`${addLabel} ${item.name}`}" in order
    assert 'role="status" aria-live="polite"' in order


def test_every_move_is_offered_without_dragging(order):
    """Dragging is one way. The menu and the keyboard are the others, and on a
    phone the menu is the main one."""
    assert "Move up" in order and "Move down" in order
    assert "onClick={() => add(item.id)}" in order  # the pool's Use
    keys = order.split("const onGripKey =", 1)[1].split("const onGripBlur", 1)[0]
    assert "event.key === ' ' || event.key === 'Enter'" in keys
    assert "event.altKey && !held" in keys
    assert "event.key === 'Escape' && held" in keys


def test_the_ends_cannot_be_moved_past(order):
    assert "disabled={index === 0}" in order
    assert "disabled={index === ids.length - 1}" in order
    step = order.split("const step = (", 1)[1].split("\n  };", 1)[0]
    assert "if (from < 0 || to < 0 || to >= ids.length) return;" in step


def test_a_move_that_changes_nothing_saves_nothing(order):
    """The old widget saved on every drop, even one that left the order as it
    was (and a drop one place down did exactly that)."""
    commit = order.split("const commit = (next: string[], change: OrderChange) => {", 1)[1].split("};", 1)[0]
    assert "if (same(next, live.current.ids)) return false;" in commit
    # every way of changing the order goes through it
    assert order.count("live.current.onChange(") == 1


def test_dragging_is_pointer_events_from_the_handle_only(order):
    """HTML5 drag had no touch, and took any dragged text for a source id."""
    assert "onPointerDown={startDrag(item.id, false)}" in order
    for html5 in ("draggable", "onDragStart", "onDrop", "dataTransfer"):
        assert html5 not in order, f"{html5}: the browser's own drag is back"
    css = _read("ui/settings.module.css")
    grip = css.split(".grip {", 1)[1].split("}", 1)[0]
    assert "touch-action: none;" in grip
    # only the handle: the rest of the row still scrolls the page
    assert css.count("touch-action: none;") == 1


def test_dropping_between_the_order_and_the_pool_works_both_ways(order):
    up = order.split("onPointerUp.current = () => {", 1)[1].split("onDragKey.current", 1)[0]
    assert "type: 'add', id: d.id, from: -1, to: d.to, by: 'drag'" in up
    assert "type: 'remove', id: d.id, from: d.from, to: -1, by: 'drag'" in up
    assert "type: 'move', id: d.id, from: d.from, to: d.to, by: 'drag'" in up


def test_a_near_miss_still_lands_where_the_row_is(order):
    """The place is worked out from where the row's middle is, not from which
    element is under the pointer."""
    target = order.split("const target = (d: Drag, y: number)", 1)[1].split("\n  };", 1)[0]
    assert "const middle = d.rect.top + d.rect.height / 2 + (y - d.startY);" in target
    assert "box.top + box.height / 2 < middle" in target


def test_there_is_one_slot_and_it_says_what_the_drop_means(order, words):
    assert order.count("data-slot=\"\"") == 1
    assert "{dropText?.(dragging.id, slotAt, shown) ?? ''}" in order
    assert "`Drop here: ${nameOf(id)} is ${verb} ${ordinal(index)}`" in words


def test_a_source_this_page_does_not_know_is_kept(view):
    assert "const unknown = full.filter((id) => !all.includes(id));" in view
    assert "model.commit([...next, ...unknown]);" in view


# ── the sources not used ──

def test_every_source_is_in_the_order_or_under_it(view, model):
    """Grouping can never lose a source: the list under the order is built
    from the groups AND every source of the kind, so one missing from the
    groups still shows."""
    assert "const grouped = [...DLCHAIN_GROUPS.flatMap((group) => group.ids), ...all];" in view
    assert "all.includes(id) && !order.includes(id)" in view
    grouped = set(re.findall(r"'([a-z_]+)'", model.split("export const DLCHAIN_GROUPS", 1)[1].split("];", 1)[0]))
    listed = set(re.findall(r"id: '([a-z_]+)'", model.split("export const HYBRID_SOURCES", 1)[1].split("];", 1)[0]))
    assert not listed - grouped, f"these sources are in no group: {sorted(listed - grouped)}"


def test_a_source_can_be_set_up_without_being_used(view, model):
    """The regression that matters: configuring a source must never depend on
    it being in an order."""
    pool = view.split("const pool: PoolItem[] =", 1)[1].split("\n  });", 1)[0]
    assert "action: hasPanel(id) ? (" in pool
    assert "onClick={() => model.openModal(id)}" in pool
    opener = model.split("    openModal(id) {", 1)[1].split("\n    },", 1)[0]
    for gate in ("music", "order", "hybrid_order", "unready"):
        assert gate not in opener, f"opening a source depends on the order ({gate})"
    assert "SOURCE_CONFIG_ID_BY_SRC[id]" in opener


def test_a_source_used_elsewhere_says_where(view, words):
    """#2026: usenet carrying every video download read as unused."""
    assert "chains.filter((chain) => chain.label !== spec.label)" in view
    assert "elsewhere.length ? usedIn(elsewhere) : statusOf(id, state).text" in view
    assert "`${use.label} #${use.pos}`" in words


# ── health ──

def test_a_row_shows_its_health_in_words(view, words):
    assert "status: statusOf(id, { ...state, unready }, { inOrder: true })," in view
    for state in ("unknown: 'Not tested yet'", "testing: 'Testing…'", "ok: 'Connected'",
                  "fail: 'Connection failed'"):
        assert state in words
    # what the server said wins over the stock words
    assert "if ((dot === 'warn' || dot === 'fail') && said)" in words


def test_test_all_sources_is_always_in_reach(view):
    """It was inside a folded section that vanished on the Video chain."""
    bar = view.split("<KindBar>", 1)[1].split("</KindBar>", 1)[0]
    assert 'id="test-all-sources-btn"' in bar
    assert "onClick={() => void model.testAll()}" in bar
    assert "hidden" not in bar


# ── the indexers ──

def test_indexers_are_their_own_group_and_never_a_row_of_an_order(view, model):
    assert '<SectionLabel title="Indexers" hint="Searched by Torrent and Usenet" />' in view
    assert "onClick={() => model.openModal('prowlarr')}" in view
    for kind in ("HYBRID_SOURCES", "AUDIOBOOK_SOURCES"):
        body = model.split(f"export const {kind}", 1)[1].split("];", 1)[0]
        assert "prowlarr" not in body
    # its status redraws from the same model the test fills
    assert "const prowlarr = statusOf('prowlarr', state);" in view
    # and its sheet does not claim a place in an order
    assert "? 'Searched by Torrent and Usenet'" in view


def test_the_filesystem_note_is_still_reachable(view):
    assert '<More label="Filesystem access: the easiest setup"' in view
    for must in ("their own download folders", "Bare-metal:", "Docker:", "Remote client"):
        assert must in view


# ── the sheet ──

def test_every_source_panel_is_parked_in_the_page(view, model):
    home = view.split("export function SourcePanelsHome", 1)[1].split("\n}", 1)[0]
    assert '<div id="source-config-home" hidden>' in home
    for part in ("soulseek", "tidal-qobuz", "hifi", "deezer-soundcloud-lidarr", "youtube",
                 "prowlarr-torrent-usenet"):
        assert f'part="{part}"' in home
    # eleven sources have a panel; External has none
    ids = model.split("export const SOURCE_CONFIG_ID_BY_SRC", 1)[1].split("};", 1)[0]
    assert len(re.findall(r"-container'", ids)) == 11 and "extto" not in ids


def test_the_panel_is_moved_into_the_sheet_never_copied():
    """Every id stays one element, so what was typed is what is saved."""
    panels = _read("tabs/source-panels.tsx")
    panel = panels.split("export function Panel(", 1)[1].split("\n}\n", 1)[0]
    assert "document.getElementById('src-modal-body')" in panel
    assert "return body ? createPortal(node, body) : node;" in panel


def test_the_sheet_is_a_dialog_that_escape_and_the_scrim_close(view):
    sheet = _read("ui/sheet.tsx")
    assert 'role="dialog"' in sheet and 'aria-modal="true"' in sheet
    assert "aria-labelledby={titleId}" in sheet
    assert "if (event.key === 'Escape') {" in sheet
    assert "onClick={() => onClose()} data-sheet-scrim" in sheet
    # focus stays inside while it is open, and goes back to what opened it
    assert "if (event.key !== 'Tab') return;" in sheet
    assert "previous.focus({ preventScroll: true });" in sheet
    assert 'id="source-config-modal"' in view and 'bodyId="src-modal-body"' in view
    # mobile.css hides the menu button under it by this class
    assert 'bodyClass="src-modal-open"' in view


def test_closing_lands_a_pending_save_and_asks_again_what_is_set_up(view):
    sheet = view.split("export function SourceSheet", 1)[1].split("\nexport function", 1)[0]
    land = sheet.split("async function land() {", 1)[1].split("\n  }", 1)[0]
    assert "_settingsAutosaveOff !== true && store.hasPending()) await store.flush();" in land
    close = sheet.split("function close() {", 1)[1].split("\n  }", 1)[0]
    assert "model.closeModal();" in close
    # "Needs setup" goes without a reload
    assert "void land().then(() => refreshReadiness(store, model));" in close


def test_the_sheet_has_one_test_that_saves_first_and_says_what_the_server_said(view):
    sheet = view.split("export function SourceSheet", 1)[1].split("\nexport function", 1)[0]
    assert sheet.count('id="src-modal-test"') == 1
    test = sheet.split("async function test() {", 1)[1].split("\n  }", 1)[0]
    assert test.index("await land();") < test.index("model.testOne(id)")
    assert "This source has no connection test." in test  # says so, never fails
    assert "good ? 'Connected.' : 'Connection failed.'" in test
    # the answer is beside the button and is announced
    assert '<StatusWords id="src-modal-result"' in sheet and " live>" in sheet
    # opening carries the last result in
    assert "known.said[id] && dot !== 'unknown' ? statusOf(id, known)" in sheet


# ── Behaviour, which was the chain's folded neighbour ──

def test_the_behaviour_settings_kept_their_ids(page):
    """They save by key now, so a moved field cannot wipe a value, but help
    search and the guard find them by id."""
    behaviour = _read("tabs/downloads/behaviour-view.tsx")
    for el in ("stream-source", "max-concurrent-downloads", "source-search-timeout",
               "retry-next-candidate", "retry-exhaustive", "retries-per-query",
               "accept-version-mismatch-fallback", "version-mismatch-min-count",
               "album-atomic-publish", "album-atomic-recover", "video-redownload-failed"):
        assert f'id="{el}"' in behaviour, el
    # music and video each behind the kind switch, both in the page at once
    assert 'id="stg-behaviour-music"' in page and 'id="stg-behaviour-video"' in page
    assert "hidden={kind !== 'music'}" in page and "hidden={kind !== 'video'}" in page


def test_hidden_pills_stay_in_the_page(page):
    """An edit that has not saved yet must survive a pill switch."""
    panel = page.split("export function ViewPanel(", 1)[1].split("\n}\n", 1)[0]
    assert "hidden={!open}" in panel
    for view_name in ("sources", "behaviour", "filtering"):
        assert f'<ViewPanel tab={{TAB}} view="{view_name}"' in page
    body = page.split("export function DownloadsPage", 1)[1].split("return (", 1)[1]
    assert "? (" not in body and "&& <" not in body, "a pill is rendered conditionally"

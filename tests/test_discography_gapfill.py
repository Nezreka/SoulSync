"""#1067 (QT3496) — discography gap-fill: "show me what my source is missing."

The dedup rule is the whole risk surface, so it gets the heavy testing:
conservative same-release matching (normalized title + compatible years,
edition markers kept so distinct editions never merge), cross-source and
cross-bucket dedup, and source-order priority for contested gaps. Endpoint
and frontend are pinned at source level: gap-fill only consults sources with
a VERIFIED enriched artist id (never fuzzy name search), fetches with
fallback disabled, and only ever appends cards.
"""

from __future__ import annotations

from pathlib import Path

from core.metadata.discography_gapfill import (
    find_gap_releases,
    gap_fill_buckets,
    normalize_title,
    release_year,
    same_release,
)

_ROOT = Path(__file__).resolve().parent.parent


def _c(title, year=None, source_id="x", **extra):
    d = {"title": title, "id": source_id}
    if year is not None:
        d["year"] = year
    d.update(extra)
    return d


# ── the matching rule ────────────────────────────────────────────────────────

def test_normalize_keeps_edition_markers():
    assert normalize_title("Geogaddi") == "geogaddi"
    assert normalize_title("  GEOGADDI!! ") == "geogaddi"
    # parens survive — editions must stay distinguishable
    assert normalize_title("Geogaddi (Deluxe Edition)") == "geogaddi (deluxe edition)"
    assert normalize_title("Geogaddi (Deluxe Edition)") != normalize_title("Geogaddi")


def test_same_release_year_window():
    assert same_release(_c("Chaosphere", 1998), _c("Chaosphere", 1998)) is True
    assert same_release(_c("Chaosphere", 1998), _c("Chaosphere", 1999)) is True   # ±1
    assert same_release(_c("Chaosphere", 1998), _c("Chaosphere", 2023)) is False  # remaster-year
    # unknown year on either side → title match decides (avoids dupes when a
    # source omits years)
    assert same_release(_c("Chaosphere"), _c("Chaosphere", 1998)) is True
    assert same_release(_c("Chaosphere", 1998), _c("Chaosphere (25th Anniversary)", 1998)) is False


def test_release_year_tolerates_shapes():
    assert release_year({"year": 1998}) == 1998
    assert release_year({"year": "1998"}) == 1998
    assert release_year({"release_date": "1998-11-10"}) == 1998
    assert release_year({"release_date": ""}) is None
    assert release_year({"year": "soon"}) is None
    assert release_year({"year": 12}) is None          # not a plausible year


# ── flat gap-fill ────────────────────────────────────────────────────────────

def test_gaps_are_only_what_base_lacks():
    base = [_c("Music Has the Right to Children", 1998), _c("Geogaddi", 2002)]
    others = {
        "itunes": [_c("Geogaddi", 2002, "it-1"),                  # base has it → dropped
                   _c("Trans Canada Highway", 2006, "it-2")],     # gap
        "musicbrainz": [_c("Trans Canada Highway", 2006, "mb-1"),  # itunes took it first
                        _c("Peel Session", 1999, "mb-2")],         # gap
    }
    gaps = find_gap_releases(base, others, ["itunes", "musicbrainz"])
    assert [(g["id"], g["gap_source"]) for g in gaps] == [
        ("it-2", "itunes"), ("mb-2", "musicbrainz")]


def test_contested_gap_goes_to_first_source_in_order():
    base = []
    others = {
        "musicbrainz": [_c("Rare EP", 2001, "mb-1")],
        "deezer": [_c("Rare EP", 2001, "dz-1")],
    }
    gaps = find_gap_releases(base, others, ["deezer", "musicbrainz"])
    assert len(gaps) == 1 and gaps[0]["id"] == "dz-1"


def test_titleless_cards_never_gap():
    gaps = find_gap_releases([], {"itunes": [_c("", 2000), {"id": "no-title"}]}, ["itunes"])
    assert gaps == []


# ── bucket-aware wrapper ─────────────────────────────────────────────────────

def test_bucket_dedup_is_cross_bucket():
    # base files it as an ALBUM; another source calls the same release a
    # single (#1064 territory) — it must NOT come back as a gap "single"
    base = {"albums": [_c("Blue Album", 2005)], "eps": [], "singles": []}
    others = {"deezer": {"albums": [], "eps": [],
                         "singles": [_c("Blue Album", 2005, "dz-1")]}}
    gaps = gap_fill_buckets(base, others, ["deezer"])
    assert gaps == {"albums": [], "eps": [], "singles": []}


def test_gap_lands_in_its_own_sources_bucket():
    base = {"albums": [], "eps": [], "singles": []}
    others = {"musicbrainz": {"albums": [_c("LP", 1999, "mb-a")],
                              "eps": [_c("An EP", 2000, "mb-e")],
                              "singles": [_c("A Single", 2001, "mb-s")]}}
    gaps = gap_fill_buckets(base, others, ["musicbrainz"])
    assert [g["id"] for g in gaps["albums"]] == ["mb-a"]
    assert [g["id"] for g in gaps["eps"]] == ["mb-e"]
    assert [g["id"] for g in gaps["singles"]] == ["mb-s"]
    assert all(g["gap_source"] == "musicbrainz"
               for b in gaps.values() for g in b)


def test_edition_uncertainty_shows_both():
    # the deliberate trade: a deluxe edition with a different year is NOT
    # merged — better a redundant badged card than a wrongly hidden one
    base = {"albums": [_c("Geogaddi", 2002)], "eps": [], "singles": []}
    others = {"musicbrainz": {"albums": [_c("Geogaddi (Deluxe Edition)", 2012, "mb-1")],
                              "eps": [], "singles": []}}
    gaps = gap_fill_buckets(base, others, ["musicbrainz"])
    assert [g["id"] for g in gaps["albums"]] == ["mb-1"]


# ── wiring contracts ─────────────────────────────────────────────────────────

def test_endpoint_is_conservative_and_additive():
    ws = (_ROOT / "web_server.py").read_text(encoding="utf-8")
    assert "/discography/gap-fill" in ws
    fn = ws[ws.index("def get_artist_discography_gap_fill"):]
    fn = fn[:fn.index("@app.route")]
    assert "artist_source_ids.get(s)" in fn          # verified-id sources only
    assert "allow_fallback=False" in fn              # no cross-source double count
    assert "fuzzy" not in fn.replace("never a fuzzy name search", "")
    # the shared id-resolver is reused by the original discography endpoint too
    assert ws.count("_resolve_artist_source_ids(") >= 3


def test_frontend_contract():
    js = (_ROOT / "webui" / "static" / "library.js").read_text(encoding="utf-8")
    assert "_loadDiscographyGapFill" in js
    assert "_gap_source" in js
    # the click override routes the card's OWN source into the tracks fetch
    click = js[js.index("if (rel._gap_source)"):]
    assert "_aat2.set('source', rel._gap_source)" in click[:200]
    # opt-in chip + persisted preference; default off
    assert "discog_gapfill" in js
    assert "gapfill-source-badge" in js
    html = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    assert 'id="gapfill-section"' in html and 'style="display:none"' in html

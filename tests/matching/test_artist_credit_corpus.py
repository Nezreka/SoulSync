"""Every artist-credit case SoulSync was ever told about, against one kernel.

Each row is a credit string, what the caller knows, and the artists it must
come out as. The source says where the case came from: an issue, a Discord
report, or the test that pinned it in the code that used to decide it on its
own (the importer, the ghost healers, the "first listed" folder rule, the
Split Artists job). A new report about splitting or joining artist names gets
a row here first.

Callers pass what they know:

- ``known``: names this context vouches for (the album's artist, the track's
  artist, the artist a download was for, a provider's partial list)
- ``library``: artists the library knows (identified ones, for most callers)
- ``evidence``: credit lists a provider recorded for the track
"""

from __future__ import annotations

import pytest

from core.matching.artist_credit import decide_credit, leads_a_band, split_names, tokens


def _lib(*names):
    keys = {n.casefold() for n in names}
    return lambda n: n.casefold() in keys


# (credit, caller knowledge, expected names, source)
CORPUS = [
    # ── one act whose name reads as a list ──────────────────────────────────
    ("Above & Beyond", {}, ["Above & Beyond"], "#2131 #2133 #2135 #2136"),
    # the library holds the band itself next to the ghost parts
    ("Above & Beyond", {"library": _lib("Above", "Beyond", "Above & Beyond")}, ["Above & Beyond"],
     "#2131: ghost 'Above' / 'Beyond' rows from an old split"),
    # a stored provider list beats ghost parts the library holds
    ("Above & Beyond", {"library": _lib("Above", "Beyond"),
                        "evidence": [("spotify credits", ["Above & Beyond"])]}, ["Above & Beyond"],
     "#2131: the track's provider credit"),
    ("Kyau & Albert", {}, ["Kyau & Albert"], "#2136"),
    ("Super8 & Tab", {}, ["Super8 & Tab"], "#2136"),
    ("Tom Petty & The Heartbreakers", {}, ["Tom Petty & The Heartbreakers"], "#2131"),
    ("Chase & Status", {}, ["Chase & Status"], "#1945"),
    ("K/DA", {}, ["K/DA"], "#2150"),
    ("AC/DC", {}, ["AC/DC"], "test_collab_credit_string_2028"),
    ("Lil Nas X", {}, ["Lil Nas X"], "the importer made 'Lil Nas' of it"),
    ("Simon & Garfunkel", {}, ["Simon & Garfunkel"], "test_importer, test_collab_first_listed"),
    ("Simon & Garfunkel", {"known": ["Simon & Garfunkel"]}, ["Simon & Garfunkel"],
     "test_collab_first_listed: listed as itself"),
    ("Hall & Oates", {}, ["Hall & Oates"], "test_importer P2-24"),
    ("Earth, Wind & Fire", {}, ["Earth, Wind & Fire"], "test_importer P2-24"),
    ("Earth, Wind & Fire", {"library": _lib("Earth")}, ["Earth, Wind & Fire"],
     "test_collab_credit_string_2028"),
    ("Earth, Wind & Fire", {"known": ["Earth, Wind & Fire"]}, ["Earth, Wind & Fire"],
     "test_local_credit_split: a band on its own album"),
    ("Florence and the Machine", {}, ["Florence and the Machine"], "test_importer P2-24"),
    ("Florence + the Machine", {}, ["Florence + the Machine"], "comparison splitters"),
    ("Tyler, The Creator", {}, ["Tyler, The Creator"], "comma splitter whitelist"),
    ("Tyler,The Creator", {}, ["Tyler,The Creator"], "comma splitter whitelist spacing"),
    ("Sammy Davis, Jr.", {}, ["Sammy Davis, Jr."], "comma splitter whitelist"),
    ("Crosby, Stills & Nash", {}, ["Crosby, Stills & Nash"], "test_local_credit_split"),
    ("Bad Company", {}, ["Bad Company"], "test_collab_credit_string_2028"),
    ("Axwell Λ Ingrosso", {}, ["Axwell Λ Ingrosso"], "#2028: Λ is part of the name"),
    ("Marina and the Diamonds", {"known": ["Marina"]}, ["Marina and the Diamonds"],
     "leads_a_band"),
    ("Bob Marley & The Wailers", {"known": ["Bob Marley"]}, ["Bob Marley & The Wailers"],
     "a83348d45: the band an artist leads"),
    ("Nick Cave & The Bad Seeds", {"known": ["Nick Cave"]}, ["Nick Cave & The Bad Seeds"],
     "a83348d45"),
    ("b'Above & Beyond'", {}, ["Above & Beyond"], "d4c89f467: bytes repr tag"),
    ("Nova & Vale", {}, ["Nova & Vale"], "lossless default: an unknown '&' stays whole"),
    ("Camellia, Toby Fox", {}, ["Camellia, Toby Fox"], "lossless default without evidence"),

    # ── several artists ─────────────────────────────────────────────────────
    ("K/DA; League Of Legends; Mimi Webb; Sophie Powers", {},
     ["K/DA", "League Of Legends", "Mimi Webb", "Sophie Powers"], "#2150"),
    ("Camellia, Toby Fox", {"library": _lib("Camellia", "Toby Fox")}, ["Camellia", "Toby Fox"],
     "comma splitter: every part known"),
    ("Camellia, Toby Fox", {"evidence": [("spotify credits", ["Camellia", "Toby Fox"])]},
     ["Camellia", "Toby Fox"], "#2150: the provider's list"),
    ("Above & Beyond feat. Zoë Johnston", {}, ["Above & Beyond", "Zoë Johnston"],
     "feat. is sure, the band stays whole"),
    ("Above & Beyond & Zoë Johnston",
     {"evidence": [("spotify credits", ["Above & Beyond", "Zoë Johnston"])]},
     ["Above & Beyond", "Zoë Johnston"], "a band inside a longer credit"),
    ("Chase & Status, Becky Hill", {"library": _lib("Chase & Status", "Becky Hill")},
     ["Chase & Status", "Becky Hill"], "#1945"),
    ("Creed, 3 Doors Down & Finger Eleven", {"known": ["3 Doors Down"]},
     ["Creed", "3 Doors Down", "Finger Eleven"], "#2028"),
    # a known name in the list proves its commas (unlike a guest cut)
    ("Creed, 3 Doors Down & Finger Eleven", {"known": ["Finger Eleven"]},
     ["Creed", "3 Doors Down", "Finger Eleven"], "#2028"),
    ("Creed, 3 Doors Down & Finger Eleven",
     {"library": _lib("Creed", "3 Doors Down"), "min_runs": 2},
     ["Creed", "3 Doors Down", "Finger Eleven"], "#2028: the library vouches for two"),
    ("Johannes Brahms; Joshua Bell", {}, ["Johannes Brahms", "Joshua Bell"], "#2028"),
    ("Пётр Ильич Чайковский, Henryk Wieniawski, Johannes Brahms, Robert Schumann; Joshua Bell",
     {}, ["Пётр Ильич Чайковский, Henryk Wieniawski, Johannes Brahms, Robert Schumann",
          "Joshua Bell"], "#2028: the ; cuts, the commas wait for evidence"),
    ("Пётр Ильич Чайковский, Henryk Wieniawski, Johannes Brahms, Robert Schumann; Joshua Bell",
     {"evidence": [("spotify credits", ["Пётр Ильич Чайковский", "Henryk Wieniawski",
                                        "Johannes Brahms", "Robert Schumann", "Joshua Bell"])]},
     ["Пётр Ильич Чайковский", "Henryk Wieniawski", "Johannes Brahms", "Robert Schumann",
      "Joshua Bell"], "#2028: the provider's list"),
    ("Samuel Barber; Ruth Golden, David Zinman, Terry Edwards, Donald Barra", {},
     ["Samuel Barber", "Ruth Golden, David Zinman, Terry Edwards, Donald Barra"], "#2028"),
    ("Samuel Barber; Ruth Golden, David Zinman, Terry Edwards, Donald Barra",
     {"library": _lib("Ruth Golden", "David Zinman", "Terry Edwards", "Donald Barra")},
     ["Samuel Barber", "Ruth Golden", "David Zinman", "Terry Edwards", "Donald Barra"],
     "#2028: the library knows the performers"),
    ("Axwell Λ Ingrosso, Axwell & Sebastian Ingrosso", {"known": ["Axwell & Sebastian Ingrosso"]},
     ["Axwell Λ Ingrosso", "Axwell & Sebastian Ingrosso"], "#2028: the duo keeps its name"),
    ("Artist A / Artist B", {}, ["Artist A", "Artist B"], "#2028: a spaced slash"),
    ("grandson & K.Flay", {"known": ["grandson"]}, ["grandson", "K.Flay"],
     "test_collab_first_listed (Discord, SeadogsBooty)"),
    ("grandson, VIC MENSA & Masked Wolf", {"known": ["grandson"]},
     ["grandson", "VIC MENSA & Masked Wolf"], "test_collab_first_listed: the lead is what counts"),
    ("Colbie Caillat feat. Lee Brice", {}, ["Colbie Caillat", "Lee Brice"], "test_collab_first_listed"),
    ("Ten Years x Someone", {}, ["Ten Years", "Someone"], "test_collab_first_listed"),
    ("Calvin Harris x Dua Lipa", {}, ["Calvin Harris", "Dua Lipa"], "test_importer"),
    ("Drake feat. Rihanna", {}, ["Drake", "Rihanna"], "test_importer"),
    ("A ft. B", {}, ["A", "B"], "test_importer"),
    ("A featuring B", {}, ["A", "B"], "test_importer"),
    # with / w/ / vs. / presents / meets sit inside band names as often as
    # they join artists: evidence decides (Nezreka's review, 1)
    ("Odetari w/ 9lives", {}, ["Odetari w/ 9lives"], "test_importer"),
    ("Odetari w/ 9lives", {"library": _lib("Odetari", "9lives")}, ["Odetari", "9lives"],
     "test_importer"),
    ("Beyoncé with JAY-Z", {"evidence": [("spotify credits", ["Beyoncé", "JAY-Z"])]},
     ["Beyoncé", "JAY-Z"], "test_artist_aliases"),
    ("Eminem vs. Jay-Z", {"library": _lib("Eminem", "Jay-Z")}, ["Eminem", "Jay-Z"],
     "test_artist_aliases"),
    ("MAN WITH A MISSION", {}, ["MAN WITH A MISSION"], "Nezreka's review, 1"),
    ("Sleeping With Sirens", {}, ["Sleeping With Sirens"], "Nezreka's review, 1"),
    ("Bear vs. Shark", {}, ["Bear vs. Shark"], "Nezreka's review, 1"),
    ("Boy Meets Girl", {}, ["Boy Meets Girl"], "Nezreka's review, 1"),
    ("Tyler, The Creator feat. Kali Uchis", {}, ["Tyler, The Creator", "Kali Uchis"],
     "Nezreka's review, 2"),
    ("Earth, Wind & Fire feat. The Emotions", {}, ["Earth, Wind & Fire", "The Emotions"],
     "Nezreka's review, 2"),
    ("Harry Connick, Jr. feat. Kelly Clarkson", {}, ["Harry Connick, Jr.", "Kelly Clarkson"],
     "Nezreka's review, 2"),
    ("Artist1; Artist2; Artist3", {}, ["Artist1", "Artist2", "Artist3"], "test_artist_aliases"),
    ("Drake & Rihanna", {"library": _lib("Drake", "Rihanna")}, ["Drake", "Rihanna"],
     "test_importer: every artist already known"),
    ("Wizkid & Kyla", {"library": _lib("Wizkid"), "min_runs": 1}, ["Wizkid", "Kyla"],
     "test_importer: a title feat. list with one known anchor"),
    ("1788-L, Deathpact", {"known": ["1788-L"]}, ["1788-L", "Deathpact"],
     "test_local_credit_split: the album artist as a part"),
    ("Øneheart, Malek", {"known": ["Øneheart"], "library": _lib("Øneheart, Malek")},
     ["Øneheart", "Malek"], "e7923c21e: anchored even with an id picked up by name"),
    ("Asian Doll & Antha Pantha", {"library": _lib("Asian Doll", "Antha Pantha")},
     ["Asian Doll", "Antha Pantha"], "test_local_credit_split"),
    ("Nelly & Florida Georgia Line", {"library": _lib("Nelly", "Florida Georgia Line")},
     ["Nelly", "Florida Georgia Line"], "test_collab_ghost_and_bytes_credits"),
    ("Skratch Bastid, Shad", {"library": _lib("Skratch Bastid", "Shad")},
     ["Skratch Bastid", "Shad"], "test_local_credit_split"),
    ("Drake, drake & DRAKE", {}, ["Drake, drake & DRAKE"], "no evidence: stays as written"),
]


@pytest.mark.parametrize("credit,knowledge,expected,source", CORPUS,
                         ids=[f"{c[0]} | {c[3]}" for c in CORPUS])
def test_corpus(credit, knowledge, expected, source):
    decision = decide_credit(
        credit,
        evidence=knowledge.get("evidence", ()),
        known=knowledge.get("known", ()),
        is_act=knowledge.get("library"),
        is_artist=knowledge.get("library"),
        min_runs=knowledge.get("min_runs"),
    )
    assert list(decision.names) == expected, f"{source}: decided by {decision.via}"


# ── evidence beats every name rule ──────────────────────────────────────────

def test_a_provider_list_beats_ghost_library_parts():
    decision = decide_credit("Nova & Vale", evidence=[("spotify credits", ["Nova & Vale"])],
                             is_artist=_lib("Nova", "Vale"))
    assert decision.names == ("Nova & Vale",) and decision.via == "spotify credits"


def test_a_provider_list_beats_the_known_bands_list():
    decision = decide_credit("Simon & Garfunkel",
                             evidence=[("spotify credits", ["Simon", "Garfunkel"])])
    assert decision.names == ("Simon", "Garfunkel")


def test_an_act_the_library_knows_whole_is_not_split_by_its_parts():
    library = _lib("Nova & Vale", "Nova", "Vale")
    assert decide_credit("Nova & Vale", is_act=library, is_artist=library).names == ("Nova & Vale",)


def test_every_decision_says_which_step_made_it():
    assert decide_credit("A feat. B").via == "structure"
    assert decide_credit("Nova & Vale").via == "whole"
    assert decide_credit("AC/DC", is_act=_lib("AC/DC")).via == "act"
    assert decide_credit("AC/DC").via == "whole"     # nothing knows it: lossless
    assert decide_credit("grandson & K.Flay", known=["grandson"]).via == "known"
    assert decide_credit("Solo").via == "single"
    assert decide_credit("").names == ()


def test_a_lookup_that_fails_never_decides():
    def broken(name):
        raise RuntimeError("db gone")
    assert decide_credit("Nova & Vale", is_act=broken, is_artist=broken).names == ("Nova & Vale",)


# ── syntax: one tokenizer for every separator ───────────────────────────────

@pytest.mark.parametrize("credit,kinds", [
    ("A; B", ["hard"]), ("A / B", ["hard"]), ("A/B", ["slash"]),
    ("A feat. B", ["marker"]), ("A x B", ["marker"]), ("A × B", ["marker"]),
    ("A, B", ["comma"]), ("A & B", ["amp"]), ("A and B", ["amp"]), ("A + B", ["amp"]),
    ("Lil Nas X", []), ("A X B", []), ("A, & B", ["comma"]),
])
def test_each_separator_has_its_strength(credit, kinds):
    assert [kind for kind, _raw in tokens(credit)[1]] == kinds


@pytest.mark.parametrize("credit,expected", [
    ("Drake feat. Wizkid & Kyla", ["Drake", "Wizkid", "Kyla"]),
    ("A, B & C", ["A", "B", "C"]),
    ("Artist,, Other", ["Artist", "Other"]),
    ("  A  ,  B  ", ["A", "B"]),
    ("Drake, drake & DRAKE", ["Drake"]),
    ("b'Above & Beyond'", ["Above", "Beyond"]),
    ("Lil Nas X", ["Lil Nas X"]),
    ("", []),
])
def test_split_names_for_comparing(credit, expected):
    assert split_names(credit) == expected


def test_leads_a_band():
    assert leads_a_band("Bob Marley & The Wailers", ["Bob Marley"])
    assert leads_a_band("Tom Petty and the Heartbreakers", ["tom petty"])
    assert not leads_a_band("Bob Marley & The Wailers feat. X", ["Bob Marley"])
    assert not leads_a_band("Bob Marley & The Wailers", ["Peter Tosh"])


# ── the same cases through the callers that used to decide on their own ─────

@pytest.mark.parametrize("credit,library,album_artist,expected", [
    ("Lil Nas X", (), None, ["Lil Nas X"]),                       # was "Lil Nas"
    ("K/DA; League Of Legends", (), None, ["K/DA", "League Of Legends"]),
    ("Above & Beyond", ("Above", "Beyond", "Above & Beyond"), None, ["Above & Beyond"]),
    ("Nova & Vale", ("Nova", "Vale", "Nova & Vale"), None, ["Nova & Vale"]),
    ("1788-L, Deathpact", ("1788-L",), "1788-L", ["1788-L", "Deathpact"]),
    ("Drake feat. Rihanna", (), None, ["Drake", "Rihanna"]),
    ("Earth, Wind & Fire", ("Earth",), "Various Artists", ["Earth, Wind & Fire"]),
])
def test_an_imported_credit_is_cut_where_sure_and_split_artists_proposes_the_rest(
        credit, library, album_artist, expected):
    """The import cuts only where the kernel is sure (feat., x, ;, |);
    Split Artists proposes the rest from the same context and splits once a
    provider credits each part on the track."""
    from core.library2.importer import _credit_names_for_import
    from core.matching.artist_credit import sure_parts
    assert _credit_names_for_import(credit) == sure_parts(credit)
    known = {n.casefold() for n in library}
    assert list(decide_credit(credit, known=[album_artist] if album_artist else [],
                              is_act=lambda n: n.casefold() in known,
                              is_artist=lambda n: n.casefold() in known).names) == expected


@pytest.mark.parametrize("credit,known,library,expected", [
    ("Above & Beyond", (), ("Above", "Beyond", "Above & Beyond"), ""),   # #2131
    ("Nova & Vale", (), ("Nova", "Vale", "Nova & Vale"), ""),
    ("Creed, 3 Doors Down & Finger Eleven", ("3 Doors Down",), (), "Creed"),
    ("Creed, 3 Doors Down & Finger Eleven", ("Finger Eleven",), (), "Creed"),
    ("MAN WITH A MISSION", ("MAN WITH A MISSION",), (), ""),
    ("Bear vs. Shark", ("Bear vs. Shark",), (), ""),
    ("Tyler, The Creator feat. Kali Uchis", (), (), "Tyler, The Creator"),
    ("Johannes Brahms; Joshua Bell", (), (), "Johannes Brahms"),
    ("K/DA; League Of Legends", (), (), "K/DA"),
    ("Simon & Garfunkel", ("Simon & Garfunkel",), (), ""),
    ("grandson & K.Flay", ("grandson",), (), "grandson"),
])
def test_first_listed_reads_credits_through_the_kernel(credit, known, library, expected):
    from core.imports.collab_artist import split_credit_primary
    keys = {n.casefold() for n in library}
    assert split_credit_primary(credit, known, lambda n: n.casefold() in keys) == expected


# ── the name a lookup searches by ───────────────────────────────────────────

@pytest.mark.parametrize("credit,expected", [
    ("Above & Beyond", "Above & Beyond"),                 # discogs cut it to "Above"
    ("K/DA", "K/DA"),
    ("Armin van Buuren, Funk Tribu", "Armin van Buuren"),  # beatport search
    ("The Sickest Squad, Frazzbass", "The Sickest Squad"),  # #1178 MusicMap 404
    ("Kendrick Lamar feat. Rihanna", "Kendrick Lamar"),
    ("Nova & Vale", "Nova & Vale"),
    ("Solo", "Solo"),
])
def test_lookup_name(credit, expected):
    from core.matching.artist_credit import lookup_name
    assert lookup_name(credit) == expected


def test_a_band_the_library_knows_is_searched_whole():
    """discogs / musicmap cut "Earth, Wind & Fire" to "Earth"; the library
    holding the band as an identified artist keeps the query whole"""
    from core.matching.artist_credit import lookup_name
    assert lookup_name("Earth, Wind & Fire", is_act=_lib("Earth, Wind & Fire")) == \
        "Earth, Wind & Fire"
    # nothing knows it: an unreadable list is searched by its first name (#1178)
    assert lookup_name("Earth, Wind & Fire") == "Earth"


@pytest.mark.parametrize("release_artist,asked,lead", [
    ("Above & Beyond", "Above & Beyond", "Above & Beyond"),
    ("Kendrick Lamar & SZA", "Kendrick Lamar", "Kendrick Lamar"),
    ("Beyoncé Feat. Kendrick Lamar", "Kendrick Lamar", "Beyoncé"),
    ("Kendrick Lamar Feat. Rihanna", "Kendrick Lamar", "Kendrick Lamar"),
])
def test_a_discogs_release_leads_with_the_kernels_primary(release_artist, asked, lead):
    # core/discogs_client.py keeps a release only when this is the artist asked for
    assert decide_credit(release_artist, known=[asked]).primary == lead


def test_comparison_split_keeps_a_spelled_out_and_where_asked():
    from core.matching.artist_aliases import split_artist_credit
    assert split_artist_credit("Marina and the Diamonds") == ["Marina and the Diamonds"]
    assert split_artist_credit("Okayracer, aldrch & poptropicaslutz!") == [
        "Okayracer", "aldrch", "poptropicaslutz!"]
    assert split_names("Marina and the Diamonds") == ["Marina", "the Diamonds"]


# ── after a cut, every piece is checked against the evidence again ─────────

def test_a_piece_a_provider_credits_as_one_artist_stays_whole_after_the_cut():
    ghosts = _lib("Nova", "Vale")
    lists = [("deezer credits", ["Nova & Vale"])]
    assert decide_credit("Nova & Vale feat. X", evidence=lists, is_artist=ghosts).names == (
        "Nova & Vale", "X")
    # without that list the ghost parts still split it: the evidence decided
    assert decide_credit("Nova & Vale feat. X", is_artist=ghosts).names == ("Nova", "Vale", "X")


def test_names_a_provider_credits_make_up_a_piece():
    lists = [("spotify credits", ["A & B", "C", "D", "E"])]
    assert decide_credit("A & B; C & D", evidence=lists).names == ("A & B", "C", "D")


def test_lazy_evidence_is_read_once_for_the_whole_and_the_pieces():
    reads = []

    def lists():
        reads.append(1)
        yield "deezer credits", ["Nova & Vale"]
    decide_credit("Nova & Vale feat. X", evidence=lists())
    assert reads == [1]


# ── more reported cases ─────────────────────────────────────────────────────

@pytest.mark.parametrize("credit,knowledge,expected,source", [
    ("John Doe, Janet Deer", {"evidence": [("spotify credits", ["John Doe", "Janet Deer"])]},
     ["John Doe", "Janet Deer"], "#215: playlist download, first listed"),
    ("John Doe, Bubba, Billy Jack", {"known": ["John Doe"]},
     ["John Doe", "Bubba", "Billy Jack"], "#215: a known name proves the list"),
    ("John Doe, Bubba, Billy Jack",
     {"evidence": [("spotify credits", ["John Doe", "Bubba", "Billy Jack"])]},
     ["John Doe", "Bubba", "Billy Jack"], "#215"),
    ("A & B", {"evidence": [("musicbrainz credits", ["A", "B"])]}, ["A", "B"],
     "#840: MusicBrainz joins artists with &"),
    ("alt‐J", {}, ["alt‐J"], "#1797: a unicode hyphen is no separator"),
    ("Earth, Wind & Fire", {"known": ["Earth"],
                            "evidence": [("spotify credits", ["Earth, Wind & Fire"])]},
     ["Earth, Wind & Fire"], "a band on an album by a namesake: the provider credit"),
    ("Chase & Status & Becky Hill", {"known": ["Chase & Status"]},
     ["Chase & Status", "Becky Hill"], "#1945: the tag spelling"),
    ("Above & Beyond", {"known": ["Above"],
                        "evidence": [("deezer credits", ["Above & Beyond"])]}, ["Above & Beyond"],
     "#2131: a ghost 'Above' as the album's artist, the provider credit"),
])
def test_more_reported_cases(credit, knowledge, expected, source):
    decision = decide_credit(credit, evidence=knowledge.get("evidence", ()),
                             known=knowledge.get("known", ()))
    assert list(decision.names) == expected, f"{source}: decided by {decision.via}"


# ── a cut is checked before and after ───────────────────────────────────────

def test_the_whole_string_is_checked_before_a_cut():
    # a library act whose name holds a cutting separator stays whole
    library = _lib("Prince with The New Power Generation")
    assert decide_credit("Prince with The New Power Generation", is_act=library).names == (
        "Prince with The New Power Generation",)
    # a provider list naming it as one artist does too
    assert decide_credit("A; B", evidence=[("spotify credits", ["A; B"])]).names == ("A; B",)
    # without either, "with" proves nothing: it stays as written
    assert decide_credit("Prince with The New Power Generation").names == (
        "Prince with The New Power Generation",)


@pytest.mark.parametrize("credit,expected", [
    ("A | B", ["A", "B"]),
    ("Ａ；Ｂ".replace("Ａ", "A").replace("Ｂ", "B"), ["A", "B"]),
    ("米津玄師、菅田将暉", ["米津玄師", "菅田将暉"]),
    ("Armin van Buuren pres. Gaia", ["Armin van Buuren", "Gaia"]),
    ("Ferry Corsten meets Someone", ["Ferry Corsten", "Someone"]),
])
def test_more_separators(credit, expected):
    assert split_names(credit) == expected


def test_context_anchor_beats_a_combined_catalogue_row_before_marker_cut():
    from core.library2.importer import _credit_names_for_import
    from core.imports.collab_artist import split_credit_primary

    library = _lib('Drake', 'Drake feat. Rihanna')
    assert _credit_names_for_import('Drake feat. Rihanna') == ['Drake', 'Rihanna']
    assert decide_credit('Drake feat. Rihanna', known=['Drake'], is_act=library,
                         is_artist=library).names == ('Drake', 'Rihanna')
    assert split_credit_primary('Drake feat. Rihanna', ['Drake'], library) == 'Drake'


@pytest.mark.parametrize('band', ['Boy Meets Girl', 'Prince with The New Power Generation',
                                 'A; B', 'A / B', 'A presents B'])
def test_provider_subact_is_protected_before_any_marker_cut(band):
    assert decide_credit(f'{band} feat. X',
                         evidence=[('spotify credits', [band])]).names == (band, 'X')


def test_context_subact_is_protected_before_marker_cut():
    assert decide_credit('Boy Meets Girl feat. X', known=['Boy Meets Girl']).names == (
        'Boy Meets Girl', 'X')


@pytest.mark.parametrize('inside', [' meets ', ' with ', '; ', ' / ', ' presents '])
@pytest.mark.parametrize('outside', [' feat. ', '; ', ' / ', ' x '])
def test_embedding_a_provider_artist_preserves_its_identity(inside, outside):
    band = f'North{inside}South'
    evidence = [('spotify credits', [band])]
    standalone = decide_credit(band, evidence=evidence)
    embedded = decide_credit(f'{band}{outside}Guest', evidence=evidence)
    assert standalone.names == (band,)
    assert embedded.names == (band, 'Guest')
    assert decide_credit(embedded.names[0], evidence=evidence).names == standalone.names


def test_library_part_checks_apply_equally_to_whole_and_nested_credits():
    library = _lib('Nova', 'Vale')
    whole = decide_credit('Nova & Vale', is_artist=library)
    nested = decide_credit('Lead feat. Nova & Vale', is_artist=library)
    assert whole.names == ('Nova', 'Vale')
    assert nested.names == ('Lead', *whole.names)



# ── no built-in list of band names ──────────────────────────────────────────

def test_with_no_data_at_all_names_alone_decide():
    """No provider credit, no identified act, no keep-whole name: only the
    name rules are left, and they are exactly as strong as the data behind
    them. The album's own artist inside a name splits it, so does a full set
    of known parts. In the running system the credit fetcher, the library's
    identified artists and Keep Whole Names stand in front of this; the
    fragment healer takes back what an old blind split left."""
    assert decide_credit("Above & Beyond", known=["Above"]).names == ("Above", "Beyond")
    assert decide_credit("Nova & Vale", is_artist=_lib("Nova", "Vale")).names == ("Nova", "Vale")
    # and an unknown name stays as it is
    assert decide_credit("Earth, Wind & Fire").names == ("Earth, Wind & Fire",)


def test_a_keep_whole_name_is_an_act_everywhere():
    class Cfg:
        def get(self, key, default=None):
            return ({"keep_whole_names": "Above & Beyond; K/DA"}
                    if key == "repair.jobs.comma_artist_splitter.settings" else default)
    cfg = Cfg()
    assert decide_credit("Above & Beyond", known=["Above"], config=cfg).names == ("Above & Beyond",)
    assert decide_credit("K/DA; Mimi Webb", is_artist=_lib("K", "DA", "Mimi Webb"),
                         config=cfg).names == ("K/DA", "Mimi Webb")


# ── found against the real test library (dev cut names after a sure cut) ────

@pytest.mark.parametrize("credit, expected", [
    ("ODESZA; RY X", ("ODESZA", "RY X")),                      # dev: "RY"
    ("Gabry Ponte; LUM!X; Prezioso", ("Gabry Ponte", "LUM!X", "Prezioso")),   # dev: "LUM!"
    ("LUM!X; Gabry Ponte; Robin Schulz", ("LUM!X", "Gabry Ponte", "Robin Schulz")),  # dev: whole
])
def test_a_capital_x_ending_a_name_is_no_marker(credit, expected):
    assert decide_credit(credit).names == expected



# ── the built-in band names list that was removed (KNOWN_ACTS) ──────────────
# every name it held, through the data-driven decision

OLD_KNOWN_ACTS = (
    'Tyler, The Creator', 'Earth, Wind & Fire', 'Earth, Wind and Fire',
    'Crosby, Stills & Nash', 'Crosby, Stills and Nash', 'Crosby, Stills, Nash & Young',
    'Crosby, Stills, Nash and Young', 'Emerson, Lake & Palmer', 'Emerson, Lake and Palmer',
    'Blood, Sweat & Tears', 'Blood, Sweat and Tears', 'Peter, Paul & Mary',
    'Peter, Paul and Mary', 'Me, Mom & Morgentaler', 'Now, Now',
    'Sammy Davis, Jr.', 'Simon & Garfunkel', 'Hall & Oates',
    'Above & Beyond', 'Chase & Status', 'Mumford & Sons',
    'Florence + the Machine', 'Florence and the Machine', 'Marina and the Diamonds',
    'Belle & Sebastian', 'Iron & Wine', 'Years & Years',
    'Kyau & Albert', 'Super8 & Tab', 'Sonny & Cher',
    'Ike & Tina Turner', 'Macklemore & Ryan Lewis', 'Tom Petty and the Heartbreakers',
    'Tom Petty & The Heartbreakers', 'Bob Marley & The Wailers', 'Nick Cave & The Bad Seeds',
    'AC/DC', 'K/DA', 'Axwell Λ Ingrosso',
    'Of Monsters and Men', 'Hootie & the Blowfish', 'Sly & the Family Stone',
    'Echo & the Bunnymen', 'Katrina and the Waves', 'Sam & Dave',
    'Brooks & Dunn', 'Big & Rich', 'Dan + Shay',
    'Matt and Kim',
)


@pytest.mark.parametrize("band", OLD_KNOWN_ACTS)
def test_every_name_the_old_list_held_stays_whole_on_the_data(band):
    from core.matching.artist_credit import credits_confirm
    words = tokens(band)[0]
    known = {w.casefold() for w in words}

    def every_word_an_artist(name):
        return name.casefold() in known
    # nothing knows it: as written
    assert decide_credit(band).names == (band,)
    # a provider credits it as one artist: whole, even with every word an artist
    whole = [("spotify credits", [band])]
    assert decide_credit(band, evidence=whole, is_artist=every_word_an_artist).names == (band,)
    # the band identified in the library: whole
    assert decide_credit(band, is_act=lambda n: n == band,
                         is_artist=every_word_an_artist).names == (band,)
    # the worst case (every word an artist, nothing else known) only proposes;
    # a provider list crediting the band never confirms cutting it
    proposed = decide_credit(band, is_artist=every_word_an_artist).names
    assert not credits_confirm(proposed, whole)



# ── the import cuts where the kernel is sure, nowhere else (Nezreka's review, 3)

@pytest.mark.parametrize("credit, expected", [
    ("Drake feat. Wizkid", ["Drake", "Wizkid"]),
    ("Drake ft. Wizkid & Kyla", ["Drake", "Wizkid & Kyla"]),
    ("A x B", ["A", "B"]),
    ("A × B", ["A", "B"]),
    ("K/DA; League Of Legends", ["K/DA", "League Of Legends"]),
    ("A | B", ["A", "B"]),
    ("Lil Nas X", ["Lil Nas X"]),
    ("Earth, Wind & Fire", ["Earth, Wind & Fire"]),
    ("1788-L, Deathpact", ["1788-L, Deathpact"]),
    ("Simon & Garfunkel", ["Simon & Garfunkel"]),
    ("Dan + Shay", ["Dan + Shay"]),
    ("AC/DC", ["AC/DC"]),
    ("Artist A / Artist B", ["Artist A / Artist B"]),
    ("MAN WITH A MISSION", ["MAN WITH A MISSION"]),
    ("Bear vs. Shark", ["Bear vs. Shark"]),
    ("Tyler, The Creator feat. Kali Uchis", ["Tyler, The Creator", "Kali Uchis"]),
])
def test_the_import_cuts_only_sure_separators(credit, expected):
    from core.library2.importer import _credit_names_for_import
    assert _credit_names_for_import(credit) == expected


def test_a_guest_or_a_semicolon_cut_proves_nothing_about_commas():
    """Nezreka's review, 2: the main act before a feat. or a ; keeps its
    commas; a known name standing in the list proves them"""
    assert decide_credit("Tyler, The Creator; Kali Uchis").names == (
        "Tyler, The Creator", "Kali Uchis")
    assert decide_credit("Creed, 3 Doors Down & Finger Eleven",
                         known=["Finger Eleven"]).names == ("Creed", "3 Doors Down", "Finger Eleven")


@pytest.mark.parametrize("band", ["MAN WITH A MISSION", "Sleeping With Sirens",
                                  "Bear vs. Shark", "Boy Meets Girl"])
def test_a_lookup_searches_a_joining_word_band_whole(band):
    """Nezreka's review, 1: beatport / musicmap searched the fragment"""
    from core.matching.artist_credit import lookup_name
    assert lookup_name(band) == band


def test_split_confirmation_requires_a_single_provider_credit_list():
    from core.matching.artist_credit import credits_confirm

    parts = ("Alpha", "Beta")
    assert not credits_confirm(parts, [("spotify credits", ["Alpha"]), ("deezer credits", ["Beta"])])
    assert not credits_confirm(parts, [("spotify credits", ["Alpha"]), ("spotify credits", ["Beta"])])
    assert credits_confirm(parts, [("spotify credits", ["Alpha", "Beta", "Guest"])])


def test_first_listed_respects_an_identified_album_artist_listed_whole():
    from core.imports.collab_artist import collab_primary

    for band, fragment in [("MAN WITH A MISSION", "MAN"), ("Sleeping With Sirens", "Sleeping"),
                           ("Bear vs. Shark", "Bear"), ("Boy Meets Girl", "Boy")]:
        assert collab_primary(band, [{"id": "band", "name": band}], [{"name": fragment}]) == ""

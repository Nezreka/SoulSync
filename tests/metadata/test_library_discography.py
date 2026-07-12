from __future__ import annotations

from core.metadata.library_discography import (
    get_library_artist_discography,
    merge_owned_releases,
    titles_represent_same_release,
)


def _discography(source, albums=None, eps=None, singles=None, success=True):
    return {
        "success": success,
        "albums": albums or [],
        "eps": eps or [],
        "singles": singles or [],
        "source": source,
        "source_priority": [source],
        "error": None if success else "not found",
    }


def test_library_discography_prefers_itunes_and_does_not_call_deezer():
    calls = []

    def lookup(artist_id, artist_name="", options=None):
        calls.append(options)
        return _discography("itunes", albums=[{"id": "it-a1", "title": "Album"}])

    result = get_library_artist_discography(
        "local-1",
        "Artist",
        {"itunes": "it-artist", "deezer": "dz-artist"},
        lookup=lookup,
    )

    assert result["source"] == "itunes"
    assert result["source_priority"] == ["itunes"]
    assert result["albums"][0]["source"] == "itunes"
    assert len(calls) == 1
    assert calls[0].source_override == "itunes"
    assert calls[0].allow_fallback is False
    assert calls[0].artist_source_ids == {"itunes": "it-artist"}


def test_library_discography_falls_back_only_to_deezer():
    calls = []

    def lookup(artist_id, artist_name="", options=None):
        calls.append(options.source_override)
        if options.source_override == "itunes":
            return _discography("itunes", success=False)
        return _discography("deezer", albums=[{"id": "dz-a1", "title": "Album"}])

    result = get_library_artist_discography(
        "local-1",
        "Artist",
        {"itunes": "it-artist", "deezer": "dz-artist", "musicbrainz": "mb-artist"},
        lookup=lookup,
    )

    assert calls == ["itunes", "deezer"]
    assert result["source"] == "deezer"
    assert result["source_priority"] == ["itunes", "deezer"]
    assert result["albums"][0]["source"] == "deezer"


def test_library_discography_returns_owned_only_when_commercial_sources_fail():
    def lookup(artist_id, artist_name="", options=None):
        return _discography(options.source_override, success=False)

    result = get_library_artist_discography(
        "local-1",
        "Artist",
        {"itunes": "it-artist", "deezer": "dz-artist"},
        owned_releases={
            "albums": [{"id": "7", "title": "Local Album", "year": 2001, "owned": True}],
            "eps": [],
            "singles": [],
        },
        owned_source_refs={"7": {"musicbrainz_release_id": "mb-release"}},
        lookup=lookup,
    )

    assert result["success"] is True
    assert result["source"] == "library"
    assert result["albums"][0]["id"] == "mb-release"
    assert result["albums"][0]["source"] == "musicbrainz"
    assert result["albums"][0]["local_album_id"] == "7"


def test_merge_keeps_external_card_when_local_provider_id_matches():
    result = merge_owned_releases(
        _discography("itunes", albums=[{"id": "it-a1", "title": "Gish (Remastered)", "source": "itunes"}]),
        {"albums": [{"id": "7", "title": "Gish", "year": 1991}], "eps": [], "singles": []},
        {"7": {"itunes_album_id": "it-a1"}},
    )

    assert [release["id"] for release in result["albums"]] == ["it-a1"]


def test_merge_does_not_duplicate_common_title_variants():
    result = merge_owned_releases(
        _discography(
            "itunes",
            albums=[
                {"id": "it-atum", "title": "ATUM", "source": "itunes"},
                {"id": "it-rotten", "title": "Rotten Apples: Greatest Hits", "source": "itunes"},
                {"id": "it-mcis", "title": "Mellon Collie and the Infinite Sadness (30th Anniversary Edition)", "source": "itunes"},
            ],
        ),
        {
            "albums": [
                {"id": "1", "title": "ATUM: A Rock Opera in Three Acts"},
                {"id": "2", "title": "(Rotten Apples) The Smashing Pumpkins Greatest Hits"},
                {"id": "3", "title": "Mellon Collie and the Infinite Sadness"},
            ],
            "eps": [],
            "singles": [],
        },
        {},
    )

    assert len(result["albums"]) == 3


def test_merge_appends_omitted_owned_release_with_best_provider_reference():
    result = merge_owned_releases(
        _discography("itunes", albums=[{"id": "it-gish", "title": "Gish", "source": "itunes"}]),
        {
            "albums": [
                {"id": "7", "title": "Gish", "year": 1991},
                {"id": "8", "title": "Zeitgeist", "year": 2007, "track_count": 12, "owned": True},
            ],
            "eps": [],
            "singles": [],
        },
        {
            "7": {"itunes_album_id": "it-gish"},
            "8": {"deezer_id": "dz-zeitgeist", "musicbrainz_release_id": "mb-zeitgeist"},
        },
    )

    assert [release["title"] for release in result["albums"]] == ["Zeitgeist", "Gish"]
    zeitgeist = result["albums"][0]
    assert zeitgeist["id"] == "dz-zeitgeist"
    assert zeitgeist["source"] == "deezer"
    assert zeitgeist["owned"] is True
    assert zeitgeist["downloadable"] is True


def test_merge_uses_non_downloadable_library_reference_as_last_resort():
    result = merge_owned_releases(
        _discography("itunes"),
        {"albums": [{"id": "9", "title": "Private Release"}], "eps": [], "singles": []},
        {},
    )

    release = result["albums"][0]
    assert release["id"] == "library:9"
    assert release["source"] == "library"
    assert release["downloadable"] is False



def test_merge_does_not_treat_same_named_single_as_album():
    result = merge_owned_releases(
        _discography("itunes", singles=[{"id": "it-s1", "title": "Home", "source": "itunes"}]),
        {"albums": [{"id": "10", "title": "Home", "year": 2001}], "eps": [], "singles": []},
        {"10": {"deezer_id": "dz-home-album"}},
    )

    assert [release["title"] for release in result["albums"]] == ["Home"]
    assert result["albums"][0]["id"] == "dz-home-album"

def test_title_match_examples_are_conservative_but_edition_aware():
    assert titles_represent_same_release("Gish", "Gish (Remastered)")
    assert titles_represent_same_release("ATUM", "ATUM: A Rock Opera in Three Acts")
    assert titles_represent_same_release(
        "Rotten Apples: Greatest Hits",
        "(Rotten Apples) The Smashing Pumpkins Greatest Hits",
    )
    assert not titles_represent_same_release("Machina", "Machina II")
    assert not titles_represent_same_release("Greatest Hits", "Greatest Hits Vol. 2")

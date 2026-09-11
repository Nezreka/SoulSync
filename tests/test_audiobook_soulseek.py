"""Tests for core/audiobook_soulseek.py — Soulseek as an audiobook source.

Hermetic: the slskd client is a stub in every test, nothing reaches the
network, and no real config is read.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.audiobook_soulseek import (
    aggregate,
    album_to_release,
    audio_files,
    cancel,
    decode_refs,
    dominant_format,
    encode_refs,
    folder_name,
    grab,
    is_enabled,
    landing_path,
    search,
    status_for,
)

BOOK = {
    "asin": "B1",
    "title": "Project Hail Mary",
    "author_names": ["Andy Weir"],
    "narrator_names": ["Ray Porter"],
    "language": "english",
    "runtime_minutes": 969,
    "series": [],
}


def _track(name, size=80_000_000):
    """80MB a file: six of those is ~64 kbps over BOOK's 16h10m runtime.

    Sized like a real audiobook on purpose. At 10MB a file the folder implied
    8 kbps, which the completeness floor hides as impossible — leaving these
    tests asserting against an empty list and proving nothing.
    """
    return SimpleNamespace(filename=name, size=size)


def _album(username="peer", path=r"@@abc\Books\Project Hail Mary [Ray Porter]",
           files=6, slots=1, queue=0):
    tracks = [_track(f"{path}\\{i:02d} - Chapter.mp3") for i in range(1, files + 1)]
    return SimpleNamespace(
        username=username, album_path=path, album_title="Project Hail Mary",
        artist="Andy Weir", track_count=len(tracks), total_size=sum(t.size for t in tracks),
        tracks=tracks, dominant_quality="mp3", year=None,
        free_upload_slots=slots, upload_speed=0, queue_length=queue,
    )


def _config(values):
    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: values.get(key, default)
    return patch("core.settings.config_manager", manager)


# ---------------------------------------------------------------------------
# Is it even wanted
# ---------------------------------------------------------------------------

def test_soulseek_only_mode_enables_it():
    with _config({"audiobooks.download_source.mode": "soulseek"}):
        assert is_enabled() is True


@pytest.mark.parametrize("mode", ["torrent", "usenet"])
def test_a_single_other_source_disables_it(mode):
    with _config({"audiobooks.download_source.mode": mode}):
        assert is_enabled() is False


def test_hybrid_enables_it_when_the_order_names_it():
    with _config({"audiobooks.download_source.mode": "hybrid",
                  "audiobooks.download_source.hybrid_order": ["torrent", "soulseek"]}):
        assert is_enabled() is True


def test_hybrid_without_it_in_the_order_disables_it():
    with _config({"audiobooks.download_source.mode": "hybrid",
                  "audiobooks.download_source.hybrid_order": ["torrent", "usenet"]}):
        assert is_enabled() is False


def test_the_music_chain_is_never_consulted():
    # A user who runs Soulseek for music and torrents for books gets exactly
    # that. Reading music's setting here would silently override them.
    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: default
    with patch("core.settings.config_manager", manager):
        is_enabled()
    assert all(not str(call.args[0]).startswith("settings.download_source")
               for call in manager.get.call_args_list)
    assert all("audiobooks." in str(call.args[0]) for call in manager.get.call_args_list)


# ---------------------------------------------------------------------------
# Reading a folder result
# ---------------------------------------------------------------------------

def test_only_audio_is_taken_from_a_folder():
    album = _album(files=2)
    album.tracks.append(_track("x\\cover.jpg"))
    album.tracks.append(_track("x\\readme.txt"))
    assert len(audio_files(album)) == 2


def test_the_folder_name_is_the_last_segment_whatever_the_slashes():
    assert folder_name(_album()) == "Project Hail Mary [Ray Porter]"
    assert folder_name(SimpleNamespace(album_path="a/b/Book", album_title="")) == "Book"


def test_the_dominant_format_is_the_commonest_extension():
    files = [{"filename": "a.mp3"}, {"filename": "b.mp3"}, {"filename": "c.m4b"}]
    assert dominant_format(files) == "mp3"


def test_a_folder_becomes_a_scorable_release():
    release = album_to_release(_album(), BOOK)
    assert release.protocol == "soulseek"
    assert release.source == "soulseek"
    assert release.title == "Project Hail Mary [Ray Porter]"
    assert release.audio_format == "mp3"
    assert release.soulseek["username"] == "peer"
    assert release.soulseek["file_count"] == 6


def test_the_wanted_narrator_is_judged_from_the_folder_name():
    # Which is the whole point: one book, one narrator, decided before the
    # download starts rather than after.
    named = _album(path="x\\Project Hail Mary read by Ray Porter")
    assert album_to_release(named, BOOK).narrator_verdict == "match"
    wrong = _album(path="x\\Project Hail Mary read by Scott Brick")
    assert album_to_release(wrong, BOOK).narrator_verdict == "mismatch"


def test_a_folder_that_names_nobody_is_not_rejected():
    # Same rule as a torrent name: most releases never say, and "unknown" is
    # allowed through. Only a folder naming somebody demonstrably else loses.
    assert album_to_release(_album(), BOOK).narrator_verdict == "unknown"


def test_a_full_cast_book_matches_on_any_of_its_narrators():
    # Comparing against only the first credit read every other cast member as
    # a different narrator and dropped the release the listener wanted.
    cast = {**BOOK, "narrator_names": ["Ray Porter", "Kate Reading"]}
    named = _album(path="x\\Project Hail Mary read by Kate Reading")
    assert album_to_release(named, cast).narrator_verdict == "match"


def test_free_slots_stand_in_for_seeders():
    assert album_to_release(_album(slots=3), BOOK).seeders == 3


def test_a_folder_with_no_audio_is_not_a_release():
    album = _album(files=0)
    album.tracks = [_track("x\\scan.jpg")]
    assert album_to_release(album, BOOK) is None


def test_somebodys_whole_library_is_not_a_release():
    # A peer sharing four hundred files in one folder is not offering a book.
    assert album_to_release(_album(files=500), BOOK) is None


def test_a_folder_with_no_owner_is_not_a_release():
    assert album_to_release(_album(username=""), BOOK) is None


def test_the_release_guid_is_unique_to_the_peer_and_folder():
    a = album_to_release(_album(username="one"), BOOK)
    b = album_to_release(_album(username="two"), BOOK)
    assert a.guid != b.guid


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------

def test_a_search_ranks_the_folders_it_finds():
    client = MagicMock()

    async def fake_search(query, **kwargs):
        return [], [_album()]

    client.search = fake_search
    found = search(BOOK, client=client)
    assert found and found[0].protocol == "soulseek"


def test_an_unreachable_slskd_returns_nothing_rather_than_raising():
    client = MagicMock()

    async def boom(query, **kwargs):
        raise RuntimeError("down")

    client.search = boom
    assert search(BOOK, client=client) == []


def test_a_search_is_skipped_entirely_when_the_chain_excludes_soulseek():
    with _config({"audiobooks.download_source.mode": "torrent"}):
        assert search(BOOK) == []


def test_a_book_with_nothing_to_search_for_asks_nobody():
    client = MagicMock()
    assert search({}, client=client) == []
    client.search.assert_not_called()


# ---------------------------------------------------------------------------
# Grabbing
# ---------------------------------------------------------------------------

def _download_client(ids=None, fail_on=()):
    client = MagicMock()
    handed = iter(ids if ids is not None else (f"id-{i}" for i in range(100)))

    async def fake_download(username, filename, size=0):
        if filename in fail_on:
            raise RuntimeError("refused")
        return next(handed, None)

    client.download = fake_download
    return client


def test_a_grab_starts_every_file_and_reports_their_ids():
    release = album_to_release(_album(files=3), BOOK)
    started = grab(release, client=_download_client())
    assert started["ok"] is True
    assert len(started["refs"]) == 3
    assert started["username"] == "peer"
    assert started["folder"] == "Project Hail Mary [Ray Porter]"


def test_a_partly_accepted_folder_is_still_started():
    # Peers drop files. The completeness gate decides whether the book is
    # whole, not whether every request was accepted up front.
    release = album_to_release(_album(files=3), BOOK)
    bad = release.soulseek["files"][1]["filename"]
    started = grab(release, client=_download_client(fail_on=(bad,)))
    assert started["ok"] is True and len(started["refs"]) == 2


def test_a_folder_nobody_accepted_is_a_failure():
    release = album_to_release(_album(files=2), BOOK)
    started = grab(release, client=_download_client(ids=[None, None]))
    assert started["ok"] is False
    assert "accepted none" in started["error"]


def test_a_release_that_is_not_from_soulseek_is_refused():
    assert grab({"protocol": "torrent"}, client=MagicMock())["ok"] is False


def test_a_grab_accepts_the_dict_form_of_a_release():
    release = album_to_release(_album(files=2), BOOK).to_dict()
    assert grab(release, client=_download_client())["ok"] is True


# ---------------------------------------------------------------------------
# Bookkeeping
# ---------------------------------------------------------------------------

def test_the_refs_survive_a_round_trip():
    packed = encode_refs(["a", "b"], "peer", "Book")
    assert decode_refs(packed) == {"username": "peer", "refs": ["a", "b"], "folder": "Book"}


def test_a_plain_id_from_before_the_json_still_decodes():
    assert decode_refs("legacy-id")["refs"] == ["legacy-id"]


def test_an_empty_client_id_decodes_to_nothing():
    assert decode_refs("")["refs"] == []


def test_the_landing_path_is_under_the_slskd_root():
    client = SimpleNamespace(download_path="/downloads")
    assert landing_path("Book", client) == "/downloads/Book"


def test_no_folder_means_no_landing_path():
    assert landing_path("", SimpleNamespace(download_path="/downloads")) == ""


# ---------------------------------------------------------------------------
# Following the transfers
# ---------------------------------------------------------------------------

def _status(ident, state, size=100, transferred=100):
    return SimpleNamespace(id=ident, state=state, size=size, transferred=transferred)


def test_a_folder_is_downloading_until_every_file_settles():
    rolled = aggregate([_status("a", "Completed, Succeeded", 100, 100),
                        _status("b", "InProgress", 100, 40)])
    assert rolled["state"] == "downloading"
    assert rolled["progress"] == 70.0


def test_a_folder_is_done_when_every_file_has_settled():
    rolled = aggregate([_status("a", "Completed, Succeeded"),
                        _status("b", "Completed, Succeeded")])
    assert rolled["state"] == "done"


def test_one_failed_file_does_not_fail_the_book():
    # The completeness gate measures what actually landed against the
    # published runtime, and is a better judge of whole than a transfer state.
    rolled = aggregate([_status("a", "Completed, Succeeded"),
                        _status("b", "Completed, Errored")])
    assert rolled["state"] == "done" and rolled["failed"] == 1


def test_every_file_failing_fails_the_book():
    rolled = aggregate([_status("a", "Completed, Cancelled"),
                        _status("b", "Completed, Errored")])
    assert rolled["state"] == "failed"


def test_a_folder_nothing_is_known_about_yet_is_queued():
    assert aggregate([], expected=5)["state"] == "queued"


def test_files_slskd_has_forgotten_still_count_against_the_total():
    # Otherwise two finished files out of thirty would report the book done.
    rolled = aggregate([_status("a", "Completed, Succeeded")], expected=30)
    assert rolled["state"] == "queued"


def test_a_status_poll_only_counts_this_books_transfers():
    client = MagicMock()

    async def everything():
        return [_status("mine-1", "Completed, Succeeded"),
                _status("someone-elses", "InProgress", 100, 0)]

    client.get_all_downloads = everything
    client.download_path = "/downloads"
    rolled = status_for(encode_refs(["mine-1"], "peer", "Book"), client=client)
    assert rolled["state"] == "done" and rolled["total"] == 1
    assert rolled["save_path"] == "/downloads/Book"


def test_an_unreachable_slskd_reports_nothing_rather_than_a_failure():
    client = MagicMock()

    async def boom():
        raise RuntimeError("down")

    client.get_all_downloads = boom
    assert status_for(encode_refs(["a"], "peer", "Book"), client=client) is None


def test_polling_a_row_with_no_refs_reports_nothing():
    assert status_for("", client=MagicMock()) is None


def test_a_cancel_stops_every_transfer_behind_the_book():
    client = MagicMock()
    stopped = []

    async def fake_cancel(ref, username=None, remove=False):
        stopped.append(ref)
        return True

    client.cancel_download = fake_cancel
    assert cancel(encode_refs(["a", "b", "c"], "peer", "Book"), client=client) is True
    assert stopped == ["a", "b", "c"]


def test_a_cancel_that_stops_nothing_reports_false():
    client = MagicMock()

    async def fake_cancel(ref, username=None, remove=False):
        return False

    client.cancel_download = fake_cancel
    assert cancel(encode_refs(["a"], "peer", "Book"), client=client) is False


def test_cancelling_a_row_with_no_refs_is_not_an_error():
    assert cancel("", client=MagicMock()) is False


@pytest.mark.parametrize("state", ["Queued, Remotely", "Requested", "Initializing"])
def test_non_transferring_files_do_not_claim_to_download(state):
    assert aggregate([_status("a", state)])["state"] == "queued"

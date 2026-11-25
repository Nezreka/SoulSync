"""a "download as-is" never takes another file's place.

it files a track under its remote folder and file name alone. two albums'
`CD1/01 - Intro.flac` shared a destination, and the move replaces whatever is
there: the second download destroyed the first, and the first's library row
went on describing audio that was no longer in the file.
"""

from core.imports.file_ops import free_destination, safe_move_file


def test_a_different_file_at_the_destination_is_not_replaced(tmp_path):
    library = tmp_path / "Transfer" / "CD1"
    library.mkdir(parents=True)
    first = library / "01 - Intro.flac"
    first.write_bytes(b"the first album's intro")
    incoming = tmp_path / "downloads" / "01 - Intro.flac"
    incoming.parent.mkdir()
    incoming.write_bytes(b"another album's intro, a different length")

    destination = free_destination(incoming, first)
    assert destination.name == "01 - Intro (2).flac"
    safe_move_file(incoming, destination)
    assert first.read_bytes() == b"the first album's intro"
    assert destination.read_bytes() == b"another album's intro, a different length"


def test_the_same_file_again_keeps_its_name(tmp_path):
    """a retry of one download is not a second file"""
    dst = tmp_path / "01 - Intro.flac"
    dst.write_bytes(b"same bytes")
    src = tmp_path / "dl.flac"
    src.write_bytes(b"same bytes")
    assert free_destination(src, dst) == dst


def test_a_free_name_is_used_as_it_is_and_taken_names_are_skipped(tmp_path):
    src = tmp_path / "dl.flac"
    src.write_bytes(b"12345")
    dst = tmp_path / "lib" / "a.flac"
    dst.parent.mkdir()
    assert free_destination(src, dst) == dst
    dst.write_bytes(b"1")
    (dst.parent / "a (2).flac").write_bytes(b"22")
    assert free_destination(src, dst).name == "a (3).flac"

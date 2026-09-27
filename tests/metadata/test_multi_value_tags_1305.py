"""#1305 (radoslav-orlov): multi-value tags written as one joined string.

a label from a provider came through as "Columbia;Grass Roots Entertainment;
BMG Direct" and was written as ONE value; genres were always one ", "-joined
value. picard writes one value per label / genre, and so do media servers
reading them. labels are always split; genres follow the multi-value setting
(off keeps today's single string for everyone who hasn't opted in).
"""

from __future__ import annotations

import inspect

from core.metadata.multi_value import genre_values, split_values


def test_a_joined_label_is_three_labels():
    assert split_values("Columbia;Grass Roots Entertainment; BMG Direct") == [
        "Columbia", "Grass Roots Entertainment", "BMG Direct"]
    assert split_values(["Columbia", "columbia", " "]) == ["Columbia"]
    assert split_values("Warner Bros./Reprise") == ["Warner Bros./Reprise"]   # '/' is part of names
    assert split_values(None) == []


def test_genres_follow_the_multi_value_setting():
    assert genre_values("Pop, R&B", True) == ["Pop", "R&B"]
    assert genre_values(["Pop", "R&B"], True) == ["Pop", "R&B"]
    assert genre_values("Pop, R&B", False) == ["Pop, R&B"]
    assert genre_values(["Pop", "R&B"], False) == ["Pop, R&B"]
    assert genre_values("", True) == []


def test_the_writers_use_the_lists():
    import core.metadata.enrichment as enrichment
    import core.metadata.source as source
    src = inspect.getsource(source)
    assert "label_values = split_values(final_label)" in src
    assert 'audio_file["LABEL"] = label_values' in src
    assert "genre_values(merged," in src
    enr = inspect.getsource(enrichment)
    assert 'audio_file["genre"] = genres_out' in enr
    assert "text=genres_out" in enr


def test_a_list_lands_as_separate_values_in_vorbis_and_id3(tmp_path):
    from mutagen.id3 import ID3, TPUB
    p = tmp_path / "t.mp3"
    p.write_bytes(b"")
    tags = ID3()
    tags.add(TPUB(encoding=3, text=split_values("Columbia;BMG Direct")))
    tags.save(str(p), v2_version=4)
    assert ID3(str(p))["TPUB"].text == ["Columbia", "BMG Direct"]

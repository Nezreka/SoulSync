"""write_tags_to_file replaces an mp3's lyrics instead of stacking a second set.

review 5 R5-A-3: a USLT frame is keyed by lang and desc. the writer added an
'eng' frame beside the file's old 'XXX' one, and read_file_tags reads the
first frame, so the tag diff said the lyrics still differed after every write.
"""
from mutagen.id3 import ID3, USLT

from core.tag_writer import read_file_tags, write_tags_to_file


def _mp3_with_lyrics(path, lang='XXX', desc=''):
    path.write_bytes((b"\xff\xfb\x90\x00" + bytes(413)) * 40)
    tags = ID3()
    tags.add(USLT(encoding=3, lang=lang, desc=desc, text='old words'))
    tags.save(str(path))
    return str(path)


def test_write_tags_to_file_replaces_the_lyrics(tmp_path):
    p = _mp3_with_lyrics(tmp_path / "a.mp3")
    res = write_tags_to_file(p, {'title': 'T', 'lyrics': 'new words'}, embed_cover=False)
    assert res.get('success'), res
    frames = ID3(p).getall('USLT')
    assert [(f.lang, f.text) for f in frames] == [('eng', 'new words')]
    assert read_file_tags(p)['lyrics'] == 'new words'


def test_a_write_without_lyrics_leaves_them(tmp_path):
    p = _mp3_with_lyrics(tmp_path / "a.mp3", lang='eng', desc='kept')
    assert write_tags_to_file(p, {'title': 'T'}, embed_cover=False).get('success')
    assert [(f.desc, f.text) for f in ID3(p).getall('USLT')] == [('kept', 'old words')]

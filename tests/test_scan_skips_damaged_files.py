"""a file the scan finds that isn't readable audio (zeroed, cut short) is left
out, not filed as a one-song album under "Unknown Artist" """

import struct
import wave

import api.database_admin as da


def _wav(path, seconds=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(struct.pack("<h", 0) * 8000 * seconds)


def test_real_audio_is_readable_and_zeros_are_not(tmp_path):
    good = tmp_path / "01 - song.wav"
    _wav(good)
    zeroed = tmp_path / "06 - The Same Rope.flac"
    zeroed.write_bytes(b"\0" * 200_000)
    empty = tmp_path / "07 - empty.flac"
    empty.write_bytes(b"")
    assert da._readable_audio(str(good))
    assert not da._readable_audio(str(zeroed))
    assert not da._readable_audio(str(empty))
    assert not da._readable_audio(str(tmp_path / "missing.flac"))


def test_the_scan_indexes_only_the_readable_files(tmp_path, monkeypatch):
    good = tmp_path / "01 - song.wav"
    _wav(good)
    zeroed = tmp_path / "06 - The Same Rope.flac"
    zeroed.write_bytes(b"\0" * 200_000)
    linked, said = [], []
    import core.library2.autolink as autolink
    monkeypatch.setattr(autolink, "link_download_into_library_v2",
                        lambda ctx, raise_on_error=True: linked.append(ctx["_final_processed_path"]) or 7)
    monkeypatch.setattr(da, "add_activity_item", lambda *a: said.append(a))
    new_ids, failed = da._index_untracked_in_place(None, [str(good), str(zeroed)], "SoulSync Deep Scan")
    assert linked == [str(good)] and new_ids == [7] and failed == 1
    assert said and "aren't readable audio" in said[0][2] and "The Same Rope" in said[0][2]

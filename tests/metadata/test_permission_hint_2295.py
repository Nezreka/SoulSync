"""#2295: a "permission denied" that says who SoulSync runs as."""

import os
from unittest.mock import patch

import pytest

from core.metadata import common


def test_the_hint_names_both_users_and_the_setting(tmp_path):
    if not hasattr(os, "getuid"):
        pytest.skip("posix ids only")
    f = tmp_path / "a.flac"
    f.write_bytes(b"x")
    real = os.stat

    class _Owned:
        def __init__(self, st):
            self.st_uid, self.st_gid, self.st_mode = 99, 100, st.st_mode

    with patch.object(common.os, "getuid", return_value=1000), \
         patch.object(common.os, "getgid", return_value=1000), \
         patch.object(common.os, "stat", side_effect=lambda p: _Owned(real(p))):
        hint = common.permission_hint(str(f))
    assert "runs as user 1000:1000" in hint
    assert "belongs to 99:100" in hint
    assert "PUID=99 PGID=100" in hint
    assert "docker exec" in hint


def test_a_denied_save_raises_with_the_hint(tmp_path):
    f = tmp_path / "a.flac"
    f.write_bytes(b"x")

    class _Audio:
        filename = str(f)

    def raw(audio, symbols, target=None):
        raise PermissionError(13, "Permission denied", target or str(f))

    with patch.object(common, "_raw_audio_save", side_effect=raw), \
         patch.object(common, "_locked_against_tag_writes", return_value=False), \
         patch.object(common, "permission_hint", return_value="HINT"):
        with pytest.raises(PermissionError) as denied:
            common.save_audio_file(_Audio(), common.get_mutagen_symbols())
    assert "HINT" in str(denied.value)

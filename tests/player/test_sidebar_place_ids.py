"""every sidebar place the player has must be one a save accepts: a place
the check refused made every setup save with a changed sidebar fail
("expected sidebar places like /player/movies or lib:5")"""

import re
from pathlib import Path

import pytest

from core.player.settings import SettingsError, _nav_ids

SECTIONS = Path(__file__).resolve().parents[2] / "webui/src/routes/player/-ui/sections.tsx"


def test_every_place_in_the_sidebar_can_be_saved():
    places = re.findall(r"to: '(/player[^']*)'", SECTIONS.read_text(encoding="utf-8"))
    assert "/player/music/playlists" in places
    assert _nav_ids(places + ["lib:3"]) == places + ["lib:3"]


@pytest.mark.parametrize("bad", ["/player/../x", "/elsewhere", "lib:x", "/player/Movies", "javascript:1"])
def test_a_place_that_isnt_one_is_still_refused(bad):
    with pytest.raises(SettingsError):
        _nav_ids([bad])

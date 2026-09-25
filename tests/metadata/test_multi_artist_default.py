"""the multi-value ARTISTS tag is on for fresh installs, and only for them.

a user compared our db to navidrome's many-to-many artists. navidrome and
jellyfin link a song to every artist through the ARTISTS tag, which we only
wrote with write_multi_artist on, and that was off by default. flipping the
default for everyone would make an existing library's next downloads tag
differently from its old files, so only a brand new config gets it.
"""

import core.settings as settings_mod
import core.tag_writer as tag_writer
from core.settings import ConfigManager


def _manager(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "music.db"))
    monkeypatch.setenv("SOULSYNC_CONFIG_PATH", str(tmp_path / "config.json"))
    return ConfigManager()


def test_fresh_install_writes_the_artists_tag(tmp_path, monkeypatch):
    cm = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(settings_mod, "config_manager", cm)
    assert cm.get("metadata_enhancement.tags.write_multi_artist") is True
    assert tag_writer._multi_artist_write_enabled() is True


def test_saved_config_without_the_setting_stays_off(tmp_path, monkeypatch):
    """an install that never touched the setting has no key saved. defaults
    aren't merged into a saved config, so it keeps tagging as before."""
    cm = _manager(tmp_path, monkeypatch)
    del cm.config_data["metadata_enhancement"]["tags"]
    cm._save_config()

    reloaded = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(settings_mod, "config_manager", reloaded)
    assert reloaded.get("metadata_enhancement.tags.write_multi_artist") is None
    assert tag_writer._multi_artist_write_enabled() is False

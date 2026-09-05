"""Every album-bundle plugin has to accept what the master worker sends.

`_bundle_plugin_kwargs()` builds one dict and `try_dispatch` splats it into
whichever plugin the source chain picked. A plugin missing one of those
parameters does not degrade: `try_dispatch` catches the `TypeError`, and
because a `TypeError` is not an `OSError` the batch is marked failed instead of
falling back to per-track. So a signature gap fails whole album downloads, and
it fails them silently for every source except the one that was tested.
"""

from __future__ import annotations

import inspect

import pytest

from core.download_plugins.torrent import TorrentDownloadPlugin
from core.download_plugins.usenet import UsenetDownloadPlugin
from core.soulseek_client import SoulseekClient

# Every key `core.downloads.master._bundle_plugin_kwargs` can emit.
MASTER_BUNDLE_KWARGS = ('quality_profile_id', 'expected_duration_seconds')


@pytest.mark.parametrize('plugin_class', [
    TorrentDownloadPlugin, UsenetDownloadPlugin, SoulseekClient,
])
def test_every_bundle_plugin_accepts_the_master_kwargs(plugin_class):
    signature = inspect.signature(plugin_class.download_album_to_staging)
    accepts_var_keyword = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )

    missing = [
        name for name in MASTER_BUNDLE_KWARGS
        if name not in signature.parameters
    ]

    assert accepts_var_keyword or not missing, (
        f"{plugin_class.__name__}.download_album_to_staging cannot accept "
        f"{missing}; the master worker sends it and the batch would fail"
    )


@pytest.mark.parametrize('plugin_class', [
    TorrentDownloadPlugin, UsenetDownloadPlugin, SoulseekClient,
])
def test_the_master_kwargs_bind_against_every_plugin(plugin_class):
    """Names alone are not enough, the call itself has to bind."""
    signature = inspect.signature(plugin_class.download_album_to_staging)

    signature.bind(
        object(), 'Album', 'Artist', '/tmp/staging', None,
        **{name: None for name in MASTER_BUNDLE_KWARGS},
    )


def test_the_kwargs_list_here_matches_what_master_actually_sends():
    """This file is only a contract if it tracks the real emitter."""
    source = inspect.getsource(
        __import__('core.downloads.master', fromlist=['master'])
    )
    start = source.index('def _bundle_plugin_kwargs')
    body = source[start:start + 900]

    for name in MASTER_BUNDLE_KWARGS:
        assert f"'{name}'" in body, f"{name} is no longer sent by master"

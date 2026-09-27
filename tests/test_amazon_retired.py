"""amazon's public t2tunes proxy is gone (#1300).

enrichment starts paused unless someone runs their own instance, and settings
can't offer it as a download source anymore.
"""

import re
from pathlib import Path

import pytest

from core.amazon_outage import amazon_enrichment_should_run

_ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8", errors="ignore")


class _Cfg(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_fresh_install_stays_paused():
    assert amazon_enrichment_should_run(_Cfg()) is False


@pytest.mark.parametrize("base", [
    None, "", "https://t2tunes.site", "https://t2tunes.site/", "http://T2Tunes.site/api",
    "https://www.t2tunes.site",
])
def test_an_opt_in_on_the_dead_host_goes_back_to_paused(base):
    cfg = _Cfg(amazon_enrichment_paused=False)
    if base is not None:
        cfg["amazon.base_url"] = base
    assert amazon_enrichment_should_run(cfg) is False


def test_a_self_hosted_instance_still_runs():
    cfg = _Cfg({"amazon_enrichment_paused": False, "amazon.base_url": "http://192.168.1.5:8080"})
    assert amazon_enrichment_should_run(cfg) is True


def test_a_self_hosted_instance_still_respects_pause():
    cfg = _Cfg({"amazon_enrichment_paused": True, "amazon.base_url": "http://192.168.1.5:8080"})
    assert amazon_enrichment_should_run(cfg) is False


def test_boot_asks_the_gate():
    src = _read("web_server.py")
    block = src.split("amazon_worker = AmazonWorker(", 1)[1].split("except Exception", 1)[0]
    assert "_amazon_enrichment_should_run(config_manager)" in block
    assert "amazon_enrichment_paused', True" not in block


def test_amazon_is_not_a_pickable_download_source():
    js = _read("webui/static/settings.js")
    index = _read("webui/index.html")
    hybrid = re.findall(r"id: '([a-z_]+)'", js.split("const HYBRID_SOURCES", 1)[1].split("];", 1)[0])
    groups = js.split("const DLCHAIN_GROUPS", 1)[1].split("];", 1)[0]
    secondary = js.split("function updateHybridSecondaryOptions(", 1)[1].split("\n}", 1)[0]
    assert "amazon" not in hybrid
    assert "'amazon'" not in groups
    assert "'amazon'" not in secondary
    assert '<option value="amazon"' not in index
    assert 'id="amazon-download-settings-container"' not in index


def test_a_saved_chain_naming_amazon_drops_it_on_load():
    js = _read("webui/static/settings.js")
    assert "const RETIRED_DOWNLOAD_SOURCES = new Set(['amazon'])" in js
    fn = js.split("function loadHybridSourceOrder(", 1)[1].split("\n}", 1)[0]
    # both the chain and the legacy primary/secondary pair
    assert fn.count("RETIRED_DOWNLOAD_SOURCES.has(id)") == 2
    assert "RETIRED_DOWNLOAD_SOURCES.has(_savedMode)" in js

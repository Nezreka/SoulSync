import types

from core.imports.pipeline import build_import_pipeline_runtime
from core.metadata.enrichment import build_metadata_enrichment_runtime


def test_build_import_pipeline_runtime_exposes_expected_contract():
    import_fields = {
        "automation_engine": object(),
        "on_download_completed": object(),
        "web_scan_manager": object(),
        "repair_worker": object(),
    }
    runtime = build_import_pipeline_runtime(**import_fields)

    assert isinstance(runtime, types.SimpleNamespace)
    for name, value in import_fields.items():
        assert hasattr(runtime, name)
        assert getattr(runtime, name) is value

    for name in (
        "mb_worker",
        "deezer_worker",
        "audiodb_worker",
        "tidal_client",
        "hifi_client",
        "qobuz_enrichment_worker",
        "lastfm_worker",
        "genius_worker",
        "spotify_enrichment_worker",
        "itunes_enrichment_worker",
    ):
        assert not hasattr(runtime, name)


def test_build_metadata_enrichment_runtime_exposes_expected_contract():
    metadata_fields = {
        "mb_worker": object(),
        "deezer_worker": object(),
        "audiodb_worker": object(),
        "tidal_client": object(),
        "hifi_client": object(),
        "qobuz_enrichment_worker": object(),
        "lastfm_worker": object(),
        "genius_worker": object(),
        "spotify_enrichment_worker": object(),
        "itunes_enrichment_worker": object(),
    }

    runtime = build_metadata_enrichment_runtime(**metadata_fields)

    assert isinstance(runtime, types.SimpleNamespace)
    for name, value in metadata_fields.items():
        assert hasattr(runtime, name)
        assert getattr(runtime, name) is value

    for name in ("automation_engine", "on_download_completed", "web_scan_manager", "repair_worker"):
        assert not hasattr(runtime, name)

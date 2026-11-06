"""the quarantine-recovery lookup runs at the start of every import. an error
in it (a locked database, a bad recovery row) aborted the import of an
ordinary download that has nothing to do with recovery. it fails open."""

from __future__ import annotations


def test_an_import_goes_on_when_the_recovery_lookup_fails(monkeypatch):
    import web_server

    def broken(*_a, **_k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr("core.acquisition.recovery.attach_recovered_staging_context", broken)
    seen = {}

    def pipeline(context_key, context, file_path, *a, **k):
        seen["context"] = dict(context)
        return "imported"

    monkeypatch.setattr("core.imports.pipeline.post_process_matched_download", pipeline)
    monkeypatch.setattr(web_server, "_import_pipeline_runtime", lambda: None)
    monkeypatch.setattr(web_server, "_build_metadata_enrichment_runtime", lambda **_k: None)

    result = web_server._post_process_matched_download("key", {"track": "Song"}, "/tmp/song.flac")

    assert result == "imported"
    assert seen["context"] == {"track": "Song"}

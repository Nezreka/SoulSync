"""web_server.py must hand the import pipeline its live dependencies.

``build_import_pipeline_runtime`` takes them as keyword arguments that all
default to None. When it moved into core.imports.pipeline, the two callers in
web_server.py kept calling it bare, so every music import ran with an empty
runtime: track_downloaded never fired (and with it every "Track Downloaded"
automation), download_quarantined never fired, the pipeline's completion
callback was dropped, a simple download never asked for a scan and the repair
worker never got its album folder. Nothing logged, because each consumer
treats a missing dependency as "not configured".

These run the real functions from web_server.py, lifted out with ast the way
the other web_server tests do, so they fail if a caller goes bare again.
"""

import ast
from pathlib import Path

import pytest

import core.imports.pipeline as import_pipeline
from core.imports.pipeline import build_import_pipeline_runtime
from core.imports.side_effects import emit_track_downloaded

_WEB_SERVER = Path(__file__).resolve().parents[2] / "web_server.py"
_FIELDS = ("automation_engine", "on_download_completed", "web_scan_manager", "repair_worker")
_GLOBALS = {
    "automation_engine": "automation_engine",
    "on_download_completed": "_on_download_completed",
    "web_scan_manager": "web_scan_manager",
    "repair_worker": "repair_worker",
}


@pytest.fixture(scope="module")
def tree():
    return ast.parse(_WEB_SERVER.read_text(encoding="utf-8"))


def _function(tree, name):
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _load(tree, name, scope):
    """exec one top-level function of web_server.py into ``scope``; every
    global it reads that the test did not supply becomes a harmless stand-in."""
    node = _function(tree, name)
    for used in {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}:
        scope.setdefault(used, object())
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(_WEB_SERVER), "exec"), scope)
    return scope[name]


def _live_scope():
    sentinels = {glob: object() for glob in _GLOBALS.values()}
    scope = dict(sentinels)
    scope["_build_import_pipeline_runtime"] = build_import_pipeline_runtime
    return scope, sentinels


def test_runtime_helper_carries_every_live_dependency(tree):
    scope, sentinels = _live_scope()
    runtime = _load(tree, "_import_pipeline_runtime", scope)()
    for field, glob in _GLOBALS.items():
        assert getattr(runtime, field) is sentinels[glob], field


def test_no_bare_builder_call_left_in_web_server(tree):
    """Only the helper may call the builder, and it passes every field."""
    calls = [
        (fn.name, call)
        for fn in tree.body if isinstance(fn, ast.FunctionDef)
        for call in ast.walk(fn)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_build_import_pipeline_runtime"
    ]
    assert [name for name, _ in calls] == ["_import_pipeline_runtime"]
    assert {kw.arg for kw in calls[0][1].keywords} == set(_FIELDS)


@pytest.mark.parametrize("wrapper, target, args", [
    ("_post_process_matched_download", "post_process_matched_download",
     ("key", {}, "/tmp/x.flac")),
    ("_post_process_matched_download_with_verification",
     "post_process_matched_download_with_verification",
     ("key", {}, "/tmp/x.flac", "task", "batch")),
])
def test_both_post_process_wrappers_pass_the_live_runtime(tree, monkeypatch, wrapper, target, args):
    seen = {}

    def capture(*a, **kw):
        seen["runtime"] = next(x for x in a if hasattr(x, "automation_engine"))

    monkeypatch.setattr(import_pipeline, target, capture)
    scope, sentinels = _live_scope()
    # no helper = the wrapper calls the builder itself; the runtime it hands
    # over is then checked the same way
    if any(isinstance(n, ast.FunctionDef) and n.name == "_import_pipeline_runtime" for n in tree.body):
        _load(tree, "_import_pipeline_runtime", scope)
    scope["_build_metadata_enrichment_runtime"] = lambda **kw: None
    scope["download_orchestrator"] = None   # the wrappers guard on it
    _load(tree, wrapper, scope)(*args)

    for field, glob in _GLOBALS.items():
        assert getattr(seen["runtime"], field) is sentinels[glob], field


def test_track_downloaded_reaches_the_engine_through_the_runtime(tree):
    """The chain the bug broke: web_server runtime -> emitter -> engine.emit."""
    class Engine:
        def __init__(self):
            self.events = []

        def emit(self, event, data):
            self.events.append((event, data))

    scope, _ = _live_scope()
    engine = scope["automation_engine"] = Engine()
    runtime = _load(tree, "_import_pipeline_runtime", scope)()

    context = {
        "track_info": {"name": "Spooky, Scary Skeletons", "artists": [{"name": "Andrew Gold"}],
                       "album": "Halloween Howls"},
        "_audio_quality": "MP3 320",
    }
    emit_track_downloaded(context, runtime.automation_engine)

    assert engine.events == [("track_downloaded", {
        "artist": "Andrew Gold", "title": "Spooky, Scary Skeletons",
        "album": "Halloween Howls", "quality": "MP3 320"})]


@pytest.mark.parametrize("album, expected", [
    ({"name": "Actions Made Audible", "id": "f669a0b5", "images": [{"url": "https://x"}]}, "Actions Made Audible"),
    ({"title": "Halloween Howls"}, "Halloween Howls"),
    ({"id": "no-name-here"}, ""),
    ("Already A Name", "Already A Name"),
])
def test_track_downloaded_carries_the_album_name_not_the_record(album, expected):
    """track_info often holds the whole album record; {album} in a
    notification showed it as a python dict"""
    class Engine:
        def __init__(self):
            self.events = []

        def emit(self, event, data):
            self.events.append((event, data))

    engine = Engine()
    emit_track_downloaded({"track_info": {"name": "t", "artists": [{"name": "a"}], "album": album},
                           "_audio_quality": "MP3-320"}, engine)
    assert engine.events[0][1]["album"] == expected

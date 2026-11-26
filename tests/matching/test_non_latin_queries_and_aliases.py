"""Regression tests for #2248 (non-Latin shortened & spaced-character queries)
and #2249 (Traditional/Simplified Chinese folding + YouTube romanized artist aliases)."""

from __future__ import annotations

from core.download_plugins.types import TrackResult
from core.matching_engine import MusicMatchingEngine
from core.spotify_client import Track


def test_shortened_original_script_query_generated_for_cyrillic_with_suffix():
    engine = MusicMatchingEngine()
    track = Track(
        id="1",
        name="Когда душе твоей больно (When Your Soul Hurts) - Arknights Image Song",
        artists=["Ploho"],
        album="Single",
        duration_ms=210000,
        popularity=0,
    )
    queries = engine.generate_download_queries(track)
    assert "Ploho Когда душе твоей больно" in queries
    assert "Когда душе твоей больно" in queries


def test_spaced_stylized_title_gets_collapsed_variant():
    engine = MusicMatchingEngine()
    track = Track(
        id="2",
        name="モ\u3000ミ\u3000ア\u3000ゲ\u3000ヲ\u3000シ\u3000ャ\u3000カ\u3000ア\u3000ゲ\u3000ヲ feat. ずんだもん",
        artists=["ぬぬぬぬぬぬぬぬぬぬぬぬぬぬぬぬ"],
        album="Single",
        duration_ms=180000,
        popularity=0,
    )
    queries = engine.generate_download_queries(track)
    assert "モミアゲヲシャカアゲヲ" in queries


def test_traditional_chinese_folds_to_simplified_in_normalize_and_queries():
    engine = MusicMatchingEngine()
    assert engine.normalize_string("無名策") == engine.normalize_string("无名策")

    track = Track(
        id="3",
        name="無名策",
        artists=["塞壬唱片-MSR"],
        album="無名策",
        duration_ms=200000,
        popularity=0,
    )
    queries = engine.generate_download_queries(track)
    assert "塞壬唱片-MSR 无名策" in queries
    assert "无名策" in queries


def test_youtube_romanized_artist_alias_passes_artist_gate(monkeypatch):
    from core.downloads import validation

    engine = MusicMatchingEngine()
    monkeypatch.setattr(validation, "matching_engine", engine)
    monkeypatch.setattr(validation, "_artist_aliases", lambda name: ["Saishinonana"] if name == "彩糸乃七" else [])

    track = Track(
        id="4",
        name="魔法少女A",
        artists=["彩糸乃七"],
        album="魔法少女A",
        duration_ms=195000,
        popularity=0,
    )
    yt = TrackResult(
        username="youtube",
        filename="eksy1uWA60g||魔法少女A - Magical GirlA",
        size=1,
        bitrate=256,
        duration=195000,
        quality="opus",
        free_upload_slots=999,
        upload_speed=999999,
        queue_length=0,
        artist="Saishinonana",
        title="魔法少女A",
    )
    accepted = validation._score_streaming_candidates([yt], track)
    assert len(accepted) == 1
    assert accepted[0].filename == yt.filename

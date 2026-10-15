"""Embedded subtitle extraction provider: ffprobe stream listing, ffmpeg SRT
extraction. ffmpeg/ffprobe are stubbed — no real binaries needed.
"""

from __future__ import annotations

import json
import os

from core.video.subtitles.providers import get_providers
from core.video.subtitles.providers.base import SubtitleQuery
from core.video.subtitles.providers import embedded as embedded_mod
from core.video.subtitles.providers.embedded import EmbeddedProvider


def _ffprobe_payload(*streams):
    return json.dumps({"streams": list(streams)}).encode()


def _stream(codec="subrip", lang="eng", hi=0, forced=0, title=""):
    return {
        "index": 3,
        "codec_name": codec,
        "disposition": {"default": 0, "forced": forced,
                        "hearing_impaired": hi},
        "tags": {"language": lang, "title": title},
    }


class _Proc:
    def __init__(self, returncode=0, stdout=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = b""


def _provider(monkeypatch, probe_out, dl_out=b"", which=None):
    """EmbeddedProvider with shutil.which + subprocess.run stubbed."""
    monkeypatch.setattr(embedded_mod.shutil, "which",
                        lambda name: which.get(name) if which else None)
    calls = []

    def _run(cmd, **kw):
        calls.append(cmd)
        if cmd[0].endswith("ffmpeg") or cmd[0].endswith("ffmpeg.exe"):
            return _Proc(0, dl_out)
        return _Proc(0, probe_out)
    monkeypatch.setattr(embedded_mod.subprocess, "run", _run)
    p = EmbeddedProvider()
    assert p.is_configured(lambda k, d=None: d)
    return p, calls


def test_registered_with_local_flags():
    providers = get_providers()
    assert "embedded" in providers
    p = providers["embedded"]
    assert p.burns_network_quota is False
    assert p.background_download_only is False
    assert p.needs_key is False


def test_is_configured_needs_both_binaries(monkeypatch, tmp_path):
    ff = tmp_path / "ffmpeg"
    ff.write_bytes(b"x")
    monkeypatch.setattr(embedded_mod.shutil, "which",
                        lambda name: str(ff) if name == "ffmpeg" else None)
    p = EmbeddedProvider()
    # ffprobe missing everywhere and no sibling next to ffmpeg
    assert p.is_configured(lambda k, d=None: d) is False


def test_search_matches_language_and_flags(monkeypatch, tmp_path):
    payload = _ffprobe_payload(
        _stream(codec="subrip", lang="eng"),
        _stream(codec="ass", lang="eng", hi=1, title="SDH"),
        _stream(codec="spa", lang="spa"),
        _stream(codec="hdmv_pgs_subtitle", lang="eng"),  # bitmap: skipped
        _stream(codec="subrip", lang=""),               # untagged: skipped
    )
    f = tmp_path / "m.mkv"
    f.write_bytes(b"x")
    p, _calls = _provider(monkeypatch, payload,
                          which={"ffmpeg": "/usr/bin/ffmpeg",
                                 "ffprobe": "/usr/bin/ffprobe"})
    # plain query: the plain stream wins, hi is pruned (shared flag_filter)
    cands = p.search(SubtitleQuery(language="en", file_path=str(f)))
    assert len(cands) == 1
    assert cands[0].language == "en" and not cands[0].hi
    assert cands[0].download_ref["sub_index"] == 0
    # prefer_hi keeps both for the scoring tiebreak
    cands = p.search(SubtitleQuery(language="en", file_path=str(f),
                                   hi_forced_pref="prefer_hi"))
    assert len(cands) == 2
    assert cands[1].hi and cands[1].title == "SDH"
    assert cands[1].download_ref["sub_index"] == 1


def test_search_never_offers_a_forced_stream_for_a_plain_row(monkeypatch, tmp_path):
    # a forced stream only covers foreign-language lines; landing it as the
    # plain <stem>.en.srt would leave near-empty subtitles.
    payload = _ffprobe_payload(_stream(codec="subrip", lang="eng", forced=1))
    f = tmp_path / "m.mkv"
    f.write_bytes(b"x")
    p, _calls = _provider(monkeypatch, payload,
                          which={"ffmpeg": "/usr/bin/ffmpeg",
                                 "ffprobe": "/usr/bin/ffprobe"})
    assert p.search(SubtitleQuery(language="en", file_path=str(f))) == []
    assert len(p.search(SubtitleQuery(language="en", forced=True,
                                      file_path=str(f)))) == 1


def test_search_needs_file_path(monkeypatch):
    p, _calls = _provider(monkeypatch, _ffprobe_payload(),
                          which={"ffmpeg": "ff", "ffprobe": "fp"})
    assert p.search(SubtitleQuery(language="en")) == []
    assert p.search(SubtitleQuery(language="en",
                                  file_path="/nope/missing.mkv")) == []


def test_search_iso639_1_passthrough(monkeypatch, tmp_path):
    payload = _ffprobe_payload(_stream(codec="subrip", lang="en"))
    f = tmp_path / "m.mkv"
    f.write_bytes(b"x")
    p, _calls = _provider(monkeypatch, payload,
                          which={"ffmpeg": "ff", "ffprobe": "fp"})
    assert len(p.search(SubtitleQuery(language="en",
                                      file_path=str(f)))) == 1


def test_download_extracts_srt(monkeypatch, tmp_path):
    srt = b"1\n00:00:01,000 --> 00:00:02,000\nhi\n"
    f = tmp_path / "m.mkv"
    f.write_bytes(b"x")
    p, calls = _provider(monkeypatch, _ffprobe_payload(), dl_out=srt,
                         which={"ffmpeg": "/usr/bin/ffmpeg",
                                "ffprobe": "/usr/bin/ffprobe"})
    # Build a candidate directly (search is covered above).
    from core.video.subtitles.providers.base import SubtitleCandidate
    cand = SubtitleCandidate(provider_id="embedded", language="en", hi=False,
                             forced=False, title="t",
                             download_ref={"file_path": str(f),
                                           "sub_index": 2})
    assert p.download(cand) == srt.decode()
    dl_cmd = calls[-1]
    assert dl_cmd[0] == "/usr/bin/ffmpeg"
    assert "0:s:2" in dl_cmd  # subtitle-relative index, not absolute


def test_download_failure_is_none(monkeypatch, tmp_path):
    f = tmp_path / "m.mkv"
    f.write_bytes(b"x")
    monkeypatch.setattr(embedded_mod.shutil, "which",
                        lambda name: "/bin/%s" % name)
    def _fail(cmd, **kw):
        return _Proc(1, b"")
    monkeypatch.setattr(embedded_mod.subprocess, "run", _fail)
    p = EmbeddedProvider()
    assert p.is_configured(lambda k, d=None: d)
    from core.video.subtitles.providers.base import SubtitleCandidate
    cand = SubtitleCandidate(provider_id="embedded", language="en", hi=False,
                             forced=False, title="t",
                             download_ref={"file_path": str(f),
                                           "sub_index": 0})
    assert p.download(cand) is None
    assert p.download("not-a-candidate") is None


def test_probe_failure_is_miss(monkeypatch, tmp_path):
    f = tmp_path / "m.mkv"
    f.write_bytes(b"x")
    monkeypatch.setattr(embedded_mod.shutil, "which",
                        lambda name: "/bin/%s" % name)
    monkeypatch.setattr(embedded_mod.subprocess, "run",
                        lambda cmd, **kw: _Proc(1, b"not json"))
    p = EmbeddedProvider()
    assert p.is_configured(lambda k, d=None: d)
    assert p.search(SubtitleQuery(language="en", file_path=str(f))) == []


def test_embedded_candidate_clears_the_chain_gate(monkeypatch, tmp_path):
    """Chain integration: an embedded candidate must survive scoring.

    Extracting from the file itself is stronger than a hash match (perfect
    timing, guaranteed language), so candidates carry hash_match=True. This
    test proves the full provider→score→rank path delivers them above the
    min-score gate — without it the provider burns an ffprobe per video and
    never fires.
    """
    from core.video.subtitles.providers import _rank_provider_candidates
    from core.video.subtitles.scoring import DEFAULT_MIN_SCORE, score_candidate

    payload = _ffprobe_payload(_stream(codec="subrip", lang="eng"))
    f = tmp_path / "m.mkv"
    f.write_bytes(b"x")
    p, _calls = _provider(monkeypatch, payload,
                          which={"ffmpeg": "/usr/bin/ffmpeg",
                                 "ffprobe": "/usr/bin/ffprobe"})
    query = SubtitleQuery(language="en", file_path=str(f),
                          filename="m.mkv")
    cands = p.search(query)
    assert len(cands) == 1
    assert cands[0].hash_match  # the gate-clearing flag
    assert score_candidate(cands[0], query) >= DEFAULT_MIN_SCORE
    ordered, (_miss_score, _miss_cand) = _rank_provider_candidates(
        p, query, DEFAULT_MIN_SCORE)
    assert len(ordered) == 1
    # _rank_provider_candidates re-searches, so compare by value, not identity
    assert ordered[0][1].download_ref == cands[0].download_ref
    assert ordered[0][1].hash_match

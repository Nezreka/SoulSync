"""Fresh-process probe for the Roadmap-3 acquisition-contract cutover.

This module is launched by ``test_contract_enforcement_deployment.py`` inside
the built SoulSync image. It uses the production Flask route, wishlist
candidate walk, acquisition persistence, and coverage endpoint. Only the
external downloader boundary is replaced by a deterministic recording client;
no provider account or music payload is required.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _run_async(awaitable):
    return asyncio.run(awaitable)


class _Registry:
    @staticmethod
    def get_spec(_username):
        return None


class _RecordingDownloader:
    registry = _Registry()

    def __init__(self) -> None:
        self.download_calls = []

    async def download(self, username, filename, size):
        self.download_calls.append((username, filename, size))
        return f"acceptance-transfer-{len(self.download_calls)}"


class _SpotifyStub:
    @staticmethod
    def get_track_details(_track_id):
        return None


@dataclass
class _Candidate:
    username: str = "acceptance-peer"
    filename: str = "Acceptance/Scheduled Song.flac"
    size: int = 2048
    confidence: float = 0.99
    title: str = "Scheduled Song"
    artist: str = "Acceptance Artist"
    album: str = "Acceptance Album"
    quality: str = "flac"
    bitrate: int = 1000
    sample_rate: int = 44100
    bit_depth: int = 16
    quality_score: float = 1.0
    upload_speed: int = 1_000_000
    queue_length: int = 0
    free_upload_slots: int = 1


@dataclass
class _Track:
    id: str = "acceptance-scheduled-track"
    name: str = "Scheduled Song"
    album: str = "Acceptance Album"
    artists: tuple[str, ...] = ("Acceptance Artist",)


def run_probe() -> None:
    if os.environ.get("SOULSYNC_PHASE3_ACCEPTANCE") != "1":
        raise RuntimeError("set SOULSYNC_PHASE3_ACCEPTANCE=1 to run this probe")

    with tempfile.TemporaryDirectory(prefix="soulsync-phase3-") as tmp:
        root = Path(tmp)
        os.environ["DATABASE_PATH"] = str(root / "phase3.db")
        os.environ["SOULSYNC_CONFIG_PATH"] = str(root / "config.json")

        # Imports intentionally happen after the isolated paths are installed.
        import web_server
        from config.settings import config_manager
        from core.downloads import candidates as candidate_walk
        from core.runtime_state import download_tasks, matched_downloads_context
        from database.music_database import close_database, get_database

        config_manager.config_data.setdefault("features", {})[
            "library_v2"
        ] = True
        config_manager.config_data["features"][
            "acquisition_contract_enforce"
        ] = True

        downloader = _RecordingDownloader()
        web_server.download_orchestrator = downloader
        web_server.run_async = _run_async
        web_server.add_activity_item = lambda *_args, **_kwargs: None

        client = web_server.app.test_client()
        manual_response = client.post(
            "/api/download",
            json={
                "username": "acceptance-peer",
                "filename": "Acceptance/Manual Song.flac",
                "size": 1024,
                "title": "Manual Song",
                "artist": "Acceptance Artist",
                "album_name": "Acceptance Album",
                "quality": "flac",
                "bitrate": 1000,
            },
        )
        assert manual_response.status_code == 200, manual_response.get_data(
            as_text=True
        )

        manual_context = matched_downloads_context[
            web_server._make_context_key(
                "acceptance-peer", "Acceptance/Manual Song.flac"
            )
        ]
        assert manual_context["_acquisition_grab_download_id"]

        task_id = "phase3-acceptance-scheduled"
        download_tasks[task_id] = {
            "status": "pending",
            "track_info": {
                "id": "acceptance-scheduled-track",
                "source": "spotify",
                "name": "Scheduled Song",
                "artists": [{"name": "Acceptance Artist"}],
                "album": {"name": "Acceptance Album"},
                "quality_profile_id": 1,
            },
            "used_sources": set(),
            "download_id": None,
            "profile_id": 1,
        }
        deps = candidate_walk.CandidatesDeps(
            download_orchestrator=downloader,
            spotify_client=_SpotifyStub(),
            run_async=_run_async,
            get_database=get_database,
            update_task_status=lambda task, status: download_tasks[task].update(
                status=status
            ),
            make_context_key=web_server._make_context_key,
            on_download_completed=lambda *_args, **_kwargs: None,
        )
        scheduled_started = candidate_walk.attempt_download_with_candidates(
            task_id,
            [_Candidate()],
            _Track(),
            batch_id="phase3-acceptance-batch",
            deps=deps,
        )
        assert scheduled_started is True

        scheduled_context = matched_downloads_context[
            web_server._make_context_key(
                "acceptance-peer", "Acceptance/Scheduled Song.flac"
            )
        ]
        assert scheduled_context["_acquisition_grab_download_id"]
        assert len(downloader.download_calls) == 2

        coverage_response = client.get(
            "/api/library/v2/acquisition/correlation-coverage?days=7"
        )
        assert coverage_response.status_code == 200, coverage_response.get_data(
            as_text=True
        )
        payload = coverage_response.get_json()
        assert payload["enforced"] is True
        assert payload["coverage"]["ready"] is True
        for consumer in ("manual", "scheduled"):
            values = payload["coverage"]["consumers"][consumer]
            assert values["prepared"] == 1
            assert values["unprepared_dispatched"] == 0
            assert values["coverage_percent"] == 100.0

        conn = get_database()._get_connection()
        try:
            request_rows = conn.execute(
                """SELECT trigger, status, COUNT(*) AS count
                     FROM acquisition_requests
                    GROUP BY trigger, status"""
            ).fetchall()
            assert {
                (row["trigger"], row["status"], row["count"])
                for row in request_rows
            } == {
                ("manual", "grabbing", 1),
                ("scheduled", "grabbing", 1),
            }
            history = conn.execute(
                """SELECT event_type, COUNT(*) AS count
                     FROM acquisition_history
                    WHERE event_type IN (
                        'manual_grab_correlated',
                        'scheduled_grab_correlated',
                        'grab_submitted'
                    )
                    GROUP BY event_type"""
            ).fetchall()
            assert {row["event_type"]: row["count"] for row in history} == {
                "manual_grab_correlated": 1,
                "scheduled_grab_correlated": 1,
                "grab_submitted": 2,
            }
        finally:
            conn.close()
            download_tasks.clear()
            matched_downloads_context.clear()
            close_database()


if __name__ == "__main__":
    run_probe()
    print("phase3 acquisition-contract deployment acceptance: PASS")

"""where soulsync is running, and the folders that fit there.

docker installs keep the container paths the compose file maps. bare metal
installs get folders next to the app, the same ones the config defaults to,
so the setup flow never hands a non-docker user an /app path (the old
wizard did).
"""

from __future__ import annotations

import os
import platform
from typing import Callable, Mapping

_TRUTHY = ("1", "true", "yes")


def is_docker(exists: Callable[[str], bool] = os.path.exists,
              environ: Mapping[str, str] = os.environ) -> bool:
    """same test core.settings uses for its own defaults"""
    return bool(exists("/.dockerenv")) or str(environ.get("SOULSYNC_IN_DOCKER", "")).lower() in _TRUTHY


# key -> (docker path, bare metal path)
_FOLDERS = {
    "download": ("/app/downloads", "./downloads"),
    "library": ("/app/Transfer", "./Transfer"),
    "staging": ("/app/Staging", "./Staging"),
    "movies": ("/media/movies", ""),
    "tv": ("/media/tv", ""),
    "youtube": ("/media/youtube", ""),
    "podcasts": ("/app/podcasts", "./podcasts"),
    "audiobooks": ("/app/audiobooks", "./audiobooks"),
}


def default_folders(docker: bool) -> dict:
    """the folder each setup row starts on. an empty value means there is no
    sensible guess and the user has to pick one"""
    return {key: (pair[0] if docker else pair[1]) for key, pair in _FOLDERS.items()}


def describe(exists: Callable[[str], bool] = os.path.exists,
             environ: Mapping[str, str] = os.environ,
             system: Callable[[], str] = platform.system) -> dict:
    docker = is_docker(exists, environ)
    return {
        "docker": docker,
        "platform": "docker" if docker else (system() or "").lower(),
        "folders": default_folders(docker),
    }

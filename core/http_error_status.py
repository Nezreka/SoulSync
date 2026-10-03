"""Read an HTTP status from provider or requests exceptions when available."""


def http_error_status(exc: Exception) -> int | None:
    """Prefer structured status fields over digits in an error message or URL."""
    status = getattr(exc, 'http_status', None)  # e.g. spotipy.SpotifyException
    if status is not None:
        return status
    return getattr(getattr(exc, 'response', None), 'status_code', None)

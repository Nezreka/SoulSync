"""Strict provider access for artist-discography lookups.

Metadata clients intentionally use best-effort behaviour in optional flows and
commonly convert transport, HTTP or parsing failures to ``[]`` or ``None``.
Artist-discography fallback needs a stricter three-state contract:

* results: the provider completed and returned releases;
* empty: the provider completed successfully but returned no releases;
* error: SoulSync could not complete communication with the provider.

The guard works on request-local shallow copies. Shared registry clients and all
non-discography callers retain their existing behaviour.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional


@dataclass(frozen=True)
class ProviderAccessFailure:
    """Normalized provider failure returned to the discography web layer."""

    source: str
    operation: str
    message: str
    status_code: int = 502


class ProviderAccessError(RuntimeError):
    """Raised when a strict discography provider operation cannot complete."""

    def __init__(self, failure: ProviderAccessFailure):
        super().__init__(failure.message)
        self.source = failure.source
        self.operation = failure.operation
        self.status_code = failure.status_code


class _FailureCapture:
    """Store the first failure observed during one provider call."""

    def __init__(self, source: str):
        self.source = _normalize_source(source)
        self.failure: Optional[ProviderAccessFailure] = None
        self._not_found_is_empty_depth = 0

    @contextmanager
    def allow_not_found_as_empty(self) -> Iterator[None]:
        """Treat 400/404/410 as empty only for an explicit entity-ID lookup."""

        self._not_found_is_empty_depth += 1
        try:
            yield
        finally:
            self._not_found_is_empty_depth -= 1

    def record(
        self,
        operation: str,
        *,
        exc: Optional[BaseException] = None,
        status_code: Optional[int] = None,
        message: Optional[str] = None,
    ) -> None:
        if self.failure is not None:
            return

        resolved_status = (
            status_code
            if isinstance(status_code, int)
            else _status_from_exception(exc) if exc is not None
            else 502
        )

        if (
            self._not_found_is_empty_depth > 0
            and resolved_status in {400, 404, 410}
        ):
            return

        detail = (message or "").strip()
        if not detail and exc is not None:
            detail = str(exc).strip() or type(exc).__name__
        if not detail:
            detail = "upstream communication failed"

        self.failure = ProviderAccessFailure(
            source=self.source,
            operation=operation,
            message=(
                f"Could not access {self.source} while loading the artist "
                f"discography ({operation}): {detail}"
            ),
            status_code=resolved_status,
        )


def _normalize_source(source: str) -> str:
    return (source or "unknown").strip().lower() or "unknown"


def _status_from_exception(exc: Optional[BaseException]) -> int:
    if exc is None:
        return 502

    response = getattr(exc, "response", None)
    status = (
        getattr(response, "status_code", None)
        or getattr(exc, "status_code", None)
        or getattr(exc, "http_status", None)
    )
    if isinstance(status, int):
        return status

    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "timeout" in name or "timed out" in text:
        return 504
    return 502


def _payload_error(payload: Any) -> tuple[Optional[int], Optional[str]]:
    """Return an upstream API error encoded inside a successful JSON response."""

    if not isinstance(payload, dict):
        return None, None

    if payload.get("success") is False:
        return 502, str(payload.get("message") or "upstream API reported failure")

    error = payload.get("error")
    if not isinstance(error, dict):
        return None, None

    error_type = str(error.get("type") or "").strip()
    message = str(error.get("message") or error_type or "upstream API error").strip()

    if error_type == "DataException":
        return None, None
    if error_type == "OAuthException":
        return 401, message
    if error_type == "QuotaException":
        return 429, message
    return 502, message


class _ResponseProxy:
    def __init__(self, response: Any, capture: _FailureCapture, operation: str):
        self._response = response
        self._capture = capture
        self._operation = operation

        status = getattr(response, "status_code", None)
        if isinstance(status, int) and status >= 400:
            capture.record(operation, status_code=status, message=f"HTTP {status}")

    def json(self, *args: Any, **kwargs: Any) -> Any:
        try:
            payload = self._response.json(*args, **kwargs)
        except Exception as exc:
            self._capture.record(
                f"{self._operation} JSON decoding",
                exc=exc,
                message="malformed upstream JSON response",
            )
            raise

        status, message = _payload_error(payload)
        if status is not None:
            self._capture.record(
                f"{self._operation} API response",
                status_code=status,
                message=message,
            )
        return payload

    def raise_for_status(self) -> Any:
        try:
            return self._response.raise_for_status()
        except Exception as exc:
            self._capture.record(self._operation, exc=exc)
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)


class _SessionProxy:
    _HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "request"}

    def __init__(self, session: Any, capture: _FailureCapture, prefix: str):
        self._session = session
        self._capture = capture
        self._prefix = prefix

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._session, name)
        if name not in self._HTTP_METHODS or not callable(attribute):
            return attribute

        def guarded(*args: Any, **kwargs: Any) -> Any:
            operation = f"{self._prefix}.{name}"
            try:
                response = attribute(*args, **kwargs)
            except Exception as exc:
                self._capture.record(operation, exc=exc)
                raise
            return _ResponseProxy(response, self._capture, operation)

        return guarded


class _CallableProxy:
    """Record exceptions from SDK-style clients such as Spotipy."""

    def __init__(self, target: Any, capture: _FailureCapture, prefix: str):
        self._target = target
        self._capture = capture
        self._prefix = prefix

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._target, name)
        if not callable(attribute):
            return attribute

        def guarded(*args: Any, **kwargs: Any) -> Any:
            operation = f"{self._prefix}.{name}"
            try:
                return attribute(*args, **kwargs)
            except Exception as exc:
                self._capture.record(operation, exc=exc)
                raise

        return guarded


class _WebSocketProxy:
    def __init__(self, websocket: Any, capture: _FailureCapture):
        self._websocket = websocket
        self._capture = capture

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._websocket, name)
        if name not in {"send", "recv", "settimeout"} or not callable(attribute):
            return attribute

        def guarded(*args: Any, **kwargs: Any) -> Any:
            try:
                return attribute(*args, **kwargs)
            except Exception as exc:
                self._capture.record(f"websocket.{name}", exc=exc)
                raise

        return guarded


def _isolate_nested_client(target: Any, capture: _FailureCapture) -> Any:
    try:
        nested_copy = copy.copy(target)
    except Exception as exc:
        raise ProviderAccessError(
            ProviderAccessFailure(
                source=capture.source,
                operation="nested client isolation",
                message=(
                    f"Could not access {capture.source} while loading the artist "
                    f"discography (nested client isolation): {exc}"
                ),
            )
        ) from exc

    nested_session = getattr(nested_copy, "session", None)
    if nested_session is not None:
        nested_copy.session = _SessionProxy(
            nested_session,
            capture,
            f"{capture.source}.http",
        )
    return nested_copy


def _instrument_client(source: str, client: Any, capture: _FailureCapture) -> Any:
    """Return an isolated client copy whose outbound boundaries record failures."""

    try:
        instrumented = copy.copy(client)
    except Exception as exc:
        raise ProviderAccessError(
            ProviderAccessFailure(
                source=_normalize_source(source),
                operation="client isolation",
                message=(
                    f"Could not access {_normalize_source(source)} while loading "
                    f"the artist discography (client isolation): {exc}"
                ),
            )
        ) from exc

    instrumented._discography_failure_capture = capture

    session = getattr(instrumented, "session", None)
    if session is not None:
        instrumented.session = _SessionProxy(
            session,
            capture,
            f"{capture.source}.http",
        )

    nested = getattr(instrumented, "_client", None)
    if nested is not None:
        instrumented._client = _isolate_nested_client(nested, capture)

    spotify_api = getattr(instrumented, "sp", None)
    if spotify_api is not None:
        instrumented.sp = _CallableProxy(spotify_api, capture, "spotify.api")

    if capture.source == "hydrabase":
        get_ws_and_lock = getattr(instrumented, "get_ws_and_lock", None)
        if callable(get_ws_and_lock):

            def guarded_get_ws_and_lock():
                try:
                    websocket, lock = get_ws_and_lock()
                except Exception as exc:
                    capture.record("websocket connection", exc=exc)
                    raise

                if websocket is None:
                    capture.record(
                        "websocket connection",
                        message="Hydrabase is disconnected",
                    )
                    return websocket, lock

                try:
                    connected = bool(websocket.connected)
                except Exception as exc:
                    capture.record("websocket connection", exc=exc)
                    return websocket, lock

                if not connected:
                    capture.record(
                        "websocket connection",
                        message="Hydrabase is disconnected",
                    )

                return _WebSocketProxy(websocket, capture), lock

            instrumented.get_ws_and_lock = guarded_get_ws_and_lock

    return instrumented


@contextmanager
def allow_provider_not_found(client: Any) -> Iterator[None]:
    """Allow not-found HTTP responses only around an explicit ID lookup."""

    capture = getattr(client, "_discography_failure_capture", None)
    if capture is None:
        yield
        return

    with capture.allow_not_found_as_empty():
        yield


def call_discography_provider(
    source: str,
    client: Any,
    callback: Callable[[Any], Any],
) -> Any:
    """Execute one provider call using an isolated, failure-aware client copy."""

    normalized_source = _normalize_source(source)
    capture = _FailureCapture(normalized_source)
    instrumented = _instrument_client(normalized_source, client, capture)

    if normalized_source == "spotify":
        auth_check = getattr(instrumented, "is_spotify_authenticated", None)
        if callable(auth_check):
            try:
                authenticated = bool(auth_check())
                free_active = bool(
                    getattr(instrumented, "_free_active", lambda: False)()
                )
            except Exception as exc:
                capture.record("authentication check", exc=exc)
                authenticated = False
                free_active = False

            if not authenticated and not free_active:
                rate_limited = False
                rate_limit_check = getattr(instrumented, "is_rate_limited", None)
                if callable(rate_limit_check):
                    try:
                        rate_limited = bool(rate_limit_check())
                    except Exception:
                        rate_limited = False

                capture.record(
                    "authentication",
                    status_code=429 if rate_limited else 401,
                    message=(
                        "Spotify is rate limited"
                        if rate_limited
                        else "Spotify authentication is unavailable"
                    ),
                )

    if capture.failure is not None:
        raise ProviderAccessError(capture.failure)

    try:
        result = callback(instrumented)
    except ProviderAccessError:
        raise
    except Exception as exc:
        capture.record("artist discography", exc=exc)
        if capture.failure is None:
            raise
        raise ProviderAccessError(capture.failure) from exc

    if capture.failure is not None:
        raise ProviderAccessError(capture.failure)

    return result

"""Standalone T2Tunes/TripleTriple API probe.

This is intentionally not wired into SoulSync's download source registry.
It validates the API surface Tubifarry targets:

    /api/status
    /api/amazon-music/search
    /api/amazon-music/metadata
    /api/amazon-music/media-from-asin

The probe can inspect returned stream metadata and optionally issue a HEAD
request against the stream URL. It does not download or decrypt audio.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://t2tunes.site"
DEFAULT_TIMEOUT_SECONDS = 30


class T2TunesError(RuntimeError):
    """Raised when the T2Tunes API returns an invalid or failed response."""


class _HttpResponse:
    def __init__(self, *, body: bytes, status_code: int, headers: Dict[str, str], url: str) -> None:
        self.body = body
        self.status_code = status_code
        self.headers = headers
        self.url = url
        self.ok = 200 <= status_code < 400
        self.text = body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if not self.ok:
            raise T2TunesError(f"HTTP {self.status_code} for {self.url}")


class _UrllibSession:
    def get(
        self,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        allow_redirects: bool = True,  # kept for test/session compatibility
        stream: bool = False,  # kept for test/session compatibility
    ) -> _HttpResponse:
        del allow_redirects, stream
        if params:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urlencode(params)}"
        return self._request("GET", url, headers=headers, timeout=timeout)

    def head(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        allow_redirects: bool = True,  # kept for test/session compatibility
    ) -> _HttpResponse:
        del allow_redirects
        return self._request("HEAD", url, headers=headers, timeout=timeout)

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> _HttpResponse:
        request = Request(url, headers=headers or {}, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                return _HttpResponse(
                    body=response.read(),
                    status_code=response.status,
                    headers={k.lower(): v for k, v in response.headers.items()},
                    url=response.url,
                )
        except HTTPError as exc:
            body = exc.read() if hasattr(exc, "read") else b""
            return _HttpResponse(
                body=body,
                status_code=exc.code,
                headers={k.lower(): v for k, v in exc.headers.items()} if exc.headers else {},
                url=exc.url,
            )
        except URLError as exc:
            raise T2TunesError(f"Network error for {url}: {exc}") from exc


@dataclass(frozen=True)
class T2TunesSearchItem:
    asin: str
    title: str
    artist_name: str
    item_type: str
    album_name: str = ""
    album_asin: str = ""
    duration_seconds: int = 0
    isrc: str = ""

    @property
    def is_album(self) -> bool:
        return "album" in self.item_type.lower()

    @property
    def is_track(self) -> bool:
        return "track" in self.item_type.lower()


@dataclass(frozen=True)
class T2TunesStreamInfo:
    asin: str
    streamable: bool
    codec: str
    format: str
    sample_rate: Optional[int]
    stream_url: str
    has_decryption_key: bool
    title: str = ""
    artist: str = ""
    album: str = ""
    isrc: str = ""


class T2TunesClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        country: str = "US",
        codec: str = "flac",
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        session: Optional[Any] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.country = country.upper()
        self.codec = codec.lower()
        self.timeout = timeout
        self.session = session or _UrllibSession()

    def status(self) -> Dict[str, Any]:
        return self._get_json("/api/status")

    def amazon_music_is_up(self) -> bool:
        status = self.status()
        return str(status.get("amazonMusic", "")).lower() == "up"

    def search(self, query: str, *, types: str = "track,album") -> List[T2TunesSearchItem]:
        data = self._get_json(
            "/api/amazon-music/search",
            params={
                "query": query,
                "types": types,
                "country": self.country,
            },
        )
        return list(_iter_search_items(data))

    def album_metadata(self, asin: str) -> Dict[str, Any]:
        return self._get_json(
            "/api/amazon-music/metadata",
            params={
                "asin": asin,
                "country": self.country,
            },
        )

    def media_from_asin(self, asin: str) -> List[T2TunesStreamInfo]:
        data = self._get_json(
            "/api/amazon-music/media-from-asin",
            params={
                "asin": asin,
                "country": self.country,
                "codec": self.codec,
            },
        )
        if isinstance(data, list):
            return [_media_response_to_stream_info(item) for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            return [_media_response_to_stream_info(data)]
        raise T2TunesError(f"Unexpected media response type: {type(data).__name__}")

    def probe_stream(self, stream_url: str) -> Dict[str, Any]:
        """Probe a stream URL without downloading audio bytes."""
        try:
            response = self.session.head(stream_url, timeout=self.timeout, allow_redirects=True)
            method = "HEAD"
            if response.status_code in (405, 403):
                response = self.session.get(
                    stream_url,
                    timeout=self.timeout,
                    allow_redirects=True,
                    stream=True,
                    headers={"Range": "bytes=0-0"},
                )
                method = "GET range"
            return {
                "ok": response.ok,
                "method": method,
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type", ""),
                "content_length": response.headers.get("content-length", ""),
                "accept_ranges": response.headers.get("accept-ranges", ""),
                "final_url": response.url,
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
            }

    def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        headers = {
            "Accept": "application/json",
            "Referer": self.base_url,
            "User-Agent": "SoulSync-T2Tunes-Probe/0.1",
        }
        try:
            response = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            response.raise_for_status()
        except Exception as exc:
            raise T2TunesError(f"Request failed for {url}: {exc}") from exc

        try:
            return response.json()
        except ValueError as exc:
            preview = response.text[:200].replace("\n", " ")
            raise T2TunesError(f"Response was not JSON for {url}: {preview!r}") from exc


def _iter_search_items(response: Any) -> Iterable[T2TunesSearchItem]:
    if not isinstance(response, dict):
        raise T2TunesError(f"Unexpected search response type: {type(response).__name__}")
    for result in response.get("results") or []:
        if not isinstance(result, dict):
            continue
        for hit in result.get("hits") or []:
            if not isinstance(hit, dict):
                continue
            document = hit.get("document")
            if not isinstance(document, dict):
                continue
            asin = str(document.get("asin") or "")
            if not asin:
                continue
            yield T2TunesSearchItem(
                asin=asin,
                title=str(document.get("title") or ""),
                artist_name=str(document.get("artistName") or ""),
                item_type=str(document.get("__type") or ""),
                album_name=str(document.get("albumName") or ""),
                album_asin=str(document.get("albumAsin") or ""),
                duration_seconds=int(document.get("duration") or 0),
                isrc=str(document.get("isrc") or ""),
            )


def _media_response_to_stream_info(item: Dict[str, Any]) -> T2TunesStreamInfo:
    stream_info = item.get("streamInfo") if isinstance(item.get("streamInfo"), dict) else {}
    tags = item.get("tags") if isinstance(item.get("tags"), dict) else {}
    return T2TunesStreamInfo(
        asin=str(item.get("asin") or ""),
        streamable=bool(item.get("streamable") if item.get("streamable") is not None else item.get("stremeable")),
        codec=str(stream_info.get("codec") or ""),
        format=str(stream_info.get("format") or ""),
        sample_rate=stream_info.get("sampleRate") if isinstance(stream_info.get("sampleRate"), int) else None,
        stream_url=str(stream_info.get("streamUrl") or ""),
        has_decryption_key=bool(item.get("decryptionKey")),
        title=str(tags.get("title") or ""),
        artist=str(tags.get("artist") or ""),
        album=str(tags.get("album") or ""),
        isrc=str(tags.get("isrc") or ""),
    )


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=True, default=lambda o: getattr(o, "__dict__", str(o))))


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a T2Tunes/TripleTriple API instance.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--country", default="US")
    parser.add_argument("--codec", default="flac", choices=("flac", "opus", "eac3"))
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")

    search_parser = sub.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--types", default="track,album")

    metadata_parser = sub.add_parser("metadata")
    metadata_parser.add_argument("asin")

    media_parser = sub.add_parser("media")
    media_parser.add_argument("asin")
    media_parser.add_argument("--probe-stream", action="store_true")

    smoke_parser = sub.add_parser("smoke")
    smoke_parser.add_argument("query")
    smoke_parser.add_argument("--probe-stream", action="store_true")

    args = parser.parse_args()
    client = T2TunesClient(args.base_url, country=args.country, codec=args.codec, timeout=args.timeout)

    if args.command == "status":
        _print_json(client.status())
        return 0

    if args.command == "search":
        _print_json([item.__dict__ for item in client.search(args.query, types=args.types)])
        return 0

    if args.command == "metadata":
        _print_json(client.album_metadata(args.asin))
        return 0

    if args.command == "media":
        streams = client.media_from_asin(args.asin)
        payload = [stream.__dict__ for stream in streams]
        if args.probe_stream:
            for index, stream in enumerate(streams):
                payload[index]["stream_probe"] = client.probe_stream(stream.stream_url) if stream.stream_url else {"ok": False}
        _print_json(payload)
        return 0

    if args.command == "smoke":
        status = client.status()
        search_items = client.search(args.query)
        first = next((item for item in search_items if item.is_track or item.is_album), None)
        media = client.media_from_asin(first.asin) if first else []
        payload = {
            "status": status,
            "amazon_music_up": str(status.get("amazonMusic", "")).lower() == "up",
            "result_count": len(search_items),
            "first_result": first.__dict__ if first else None,
            "media": [stream.__dict__ for stream in media[:3]],
        }
        if args.probe_stream and media and media[0].stream_url:
            payload["first_stream_probe"] = client.probe_stream(media[0].stream_url)
        _print_json(payload)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())

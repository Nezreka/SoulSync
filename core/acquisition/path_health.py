"""Read-only remote path mapping diagnostics for Acquisition imports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple


MAPPINGS_KEY = "download_source.usenet_path_mappings"


def _normalized(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").rstrip("/")


def _is_readable_path(value: Any) -> bool:
    try:
        path = Path(str(value or ""))
        return path.is_dir() or path.is_file()
    except OSError:
        return False


@dataclass(frozen=True)
class MappingConfigurationHealth:
    configured_count: int
    valid_count: int
    readable_target_count: int
    invalid_indexes: Tuple[int, ...]

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "configured_count": self.configured_count,
            "valid_count": self.valid_count,
            "readable_target_count": self.readable_target_count,
            "invalid_indexes": list(self.invalid_indexes),
            "healthy": (
                not self.invalid_indexes
                and self.valid_count == self.readable_target_count
            ),
        }


@dataclass(frozen=True)
class ReportedPathHealth:
    status: str
    readable: bool
    remapped: bool
    matching_mapping: bool

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "readable": self.readable,
            "remapped": self.remapped,
            "matching_mapping": self.matching_mapping,
        }


def _configured_mappings(config_get: Callable[..., Any]) -> Tuple[Any, ...]:
    raw = config_get(MAPPINGS_KEY, None) or []
    return tuple(raw) if isinstance(raw, (list, tuple)) else (raw,)


def inspect_mapping_configuration(
    config_get: Callable[..., Any],
) -> MappingConfigurationHealth:
    mappings = _configured_mappings(config_get)
    valid = 0
    readable = 0
    invalid = []
    for index, mapping in enumerate(mappings):
        if not isinstance(mapping, Mapping):
            invalid.append(index)
            continue
        remote = _normalized(mapping.get("from"))
        local = str(mapping.get("to") or "").strip()
        if not remote or not local:
            invalid.append(index)
            continue
        valid += 1
        if _is_readable_path(local):
            readable += 1
    return MappingConfigurationHealth(
        configured_count=len(mappings),
        valid_count=valid,
        readable_target_count=readable,
        invalid_indexes=tuple(invalid),
    )


def inspect_reported_path(
    reported_path: Optional[str],
    *,
    config_get: Callable[..., Any],
    resolver: Optional[Callable[..., Optional[str]]] = None,
) -> ReportedPathHealth:
    reported = str(reported_path or "").strip()
    if not reported:
        return ReportedPathHealth("missing", False, False, False)
    normalized_reported = _normalized(reported)
    matching_mapping = False
    for mapping in _configured_mappings(config_get):
        if not isinstance(mapping, Mapping):
            continue
        remote = _normalized(mapping.get("from"))
        if remote and (
            normalized_reported == remote
            or normalized_reported.startswith(remote + "/")
        ):
            matching_mapping = True
            break

    if resolver is None:
        from core.download_plugins.album_bundle import resolve_reported_save_path
        resolver = resolve_reported_save_path
    resolved = str(resolver(reported, config_get) or reported)
    readable = _is_readable_path(resolved)
    remapped = _normalized(resolved) != normalized_reported
    if readable and remapped:
        status = "mapped"
    elif readable:
        status = "direct"
    elif matching_mapping:
        status = "mapping_unavailable"
    else:
        status = "unreadable"
    return ReportedPathHealth(status, readable, remapped, matching_mapping)


__all__ = [
    "MAPPINGS_KEY",
    "MappingConfigurationHealth",
    "ReportedPathHealth",
    "inspect_mapping_configuration",
    "inspect_reported_path",
]

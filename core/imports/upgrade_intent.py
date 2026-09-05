"""Runtime-only authority for destructive Library-v2 upgrade imports.

JSON may name an entity at an API boundary, but it can never construct the
sealed object below.  Only server code that has resolved the entity may attach
one to an in-memory import context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


CONTEXT_KEY = "_server_library_v2_upgrade_intent"
_SEAL = object()
_CLIENT_AUTHORITY_KEYS = frozenset({
    "source_info",
    "lib2_entity",
    "quality_profile",
    "quality_profile_id",
    "upgrade_check",
    CONTEXT_KEY,
})


@dataclass(frozen=True, slots=True)
class LibraryV2UpgradeIntent:
    track_id: int
    origin: str
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _SEAL or self.track_id <= 0:
            raise ValueError("invalid Library-v2 upgrade intent")


def issue_upgrade_intent(track_id: Any, *, origin: str) -> LibraryV2UpgradeIntent:
    """Mint a process-local intent after a server-side entity lookup."""
    return LibraryV2UpgradeIntent(int(track_id), str(origin), _SEAL)


def attach_upgrade_intent(context: dict, intent: Any) -> bool:
    if not is_upgrade_intent(intent):
        return False
    context[CONTEXT_KEY] = intent
    return True


def carry_upgrade_intent(context: Any, task: dict) -> bool:
    """Hand a resolved intent from an import context to the task that carries it.

    ACQ-02: ``_pipeline_context`` seals the intent onto the top level of the
    context, but the candidate dispatch and staging consumers read it off the
    TASK. So the first pipeline call had it while the quarantine-retry task and
    the after-restart rebuild did not — and the replacement import ran with no
    track lock, no upgrade/profile snapshot and no comparison against the
    existing primary file. A genuine FLAC→FLAC upgrade then met the ordinary
    same-format overwrite guard, which treats an identical format as equivalent,
    and was discarded.

    This only moves an intent that is already sealed; it can no more mint one
    than a client can.
    """
    intent = get_upgrade_intent(context)
    if intent is None:
        return False
    task[CONTEXT_KEY] = intent
    return True


def get_upgrade_intent(context: Any) -> LibraryV2UpgradeIntent | None:
    if not isinstance(context, Mapping):
        return None
    value = context.get(CONTEXT_KEY)
    return value if is_upgrade_intent(value) else None


def is_upgrade_intent(value: Any) -> bool:
    return (
        isinstance(value, LibraryV2UpgradeIntent)
        and value._seal is _SEAL
        and value.track_id > 0
    )


def sanitize_client_import_metadata(value: Any) -> Any:
    """Recursively remove fields that may only be supplied by the server."""
    if isinstance(value, Mapping):
        return {
            key: sanitize_client_import_metadata(item)
            for key, item in value.items()
            if isinstance(key, str)
            and key not in _CLIENT_AUTHORITY_KEYS
            and not key.startswith("lib2_")
        }
    if isinstance(value, list):
        return [sanitize_client_import_metadata(item) for item in value]
    return value


__all__ = [
    "CONTEXT_KEY",
    "LibraryV2UpgradeIntent",
    "attach_upgrade_intent",
    "carry_upgrade_intent",
    "get_upgrade_intent",
    "is_upgrade_intent",
    "issue_upgrade_intent",
    "sanitize_client_import_metadata",
]

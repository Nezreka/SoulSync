"""Why a download candidate was taken or passed over.

``validation.get_valid_candidates`` decides which search hits are worth
downloading and only ever hands back the survivors. ``evaluate_candidates``
runs the same decision and keeps a :class:`Decision` for every row, so a UI
(the candidate inspector) or a record (download_decisions) can say why.

Codes are a public contract: the UI keys labels off them and stored decisions
outlive releases. Add new ones; never rename or reuse one.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Optional

# code -> stage. The stage groups codes for display ("identity" problems are a
# different kind of wrong than "quality" ones) and drives override warnings.
REASON_CODES = {
    'accepted': 'decision',
    'preview': 'preview',
    'duration_mismatch': 'duration',
    'artist_mismatch': 'identity',
    'artist_unverified': 'identity',
    'match_weak': 'identity',
    'outranked': 'identity',
    'version_conflict': 'version',
    'below_profile': 'quality',
    'peer_queue': 'availability',
    'quarantined': 'policy',
    'too_large': 'policy',
    'blacklisted': 'policy',
    'duplicate': 'decision',
    'unexplained': 'decision',
}

STAGES = frozenset(REASON_CODES.values())

# Rejections that mean "probably not the song you asked for", as opposed to
# "the song, but not how you wanted it". Overriding one of these deserves a
# louder warning.
IDENTITY_STAGES = frozenset({'identity', 'version', 'duration', 'preview'})


@dataclass(frozen=True)
class Decision:
    accepted: bool
    code: str
    detail: str = ''
    stage: str = ''
    score: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            'accepted': self.accepted,
            'code': self.code,
            'detail': self.detail,
            'stage': self.stage,
            'score': None if self.score is None else round(float(self.score), 3),
        }


def _stage(code: str) -> str:
    try:
        return REASON_CODES[code]
    except KeyError:
        raise ValueError(f"unknown decision code {code!r}") from None


def accept(score: Optional[float] = None, detail: str = '') -> Decision:
    return Decision(True, 'accepted', detail, _stage('accepted'), _as_score(score))


def reject(code: str, detail: str = '', score: Optional[float] = None) -> Decision:
    if code == 'accepted':
        raise ValueError("reject() cannot use the 'accepted' code")
    return Decision(False, code, detail, _stage(code), _as_score(score))


def _as_score(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def rejection_counts(decisions: Iterable[Decision]) -> dict:
    """{code: n} over rejected decisions, most common first."""
    counts = Counter(d.code for d in decisions if not d.accepted)
    return dict(counts.most_common())


def format_duration(ms) -> str:
    try:
        total = int(round(float(ms) / 1000.0))
    except (TypeError, ValueError):
        return '?'
    if total <= 0:
        return '?'
    return f"{total // 60}:{total % 60:02d}"

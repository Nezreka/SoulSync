"""Source-agnostic acquisition Decision Engine (audit chapter 12)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from core.acquisition.candidates import ReleaseCandidate
from core.acquisition.capabilities import get_source_capabilities
from core.acquisition.requests import AcquisitionRequest
from core.quality.model import AudioQuality, QualityTarget, rank_candidate
from core.quality.selection import targets_from_profile
from core.downloads.source_policy import SourcePolicy


ENGINE_VERSION = "acquisition-decision/1"


@dataclass(frozen=True)
class DecisionReason:
    specification: str
    code: str
    severity: str
    message: str
    overridable: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "specification": self.specification,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "overridable": self.overridable,
        }


@dataclass(frozen=True)
class CatalogContext:
    artist: Optional[str] = None
    release_title: Optional[str] = None
    edition: Optional[str] = None
    track_count: Optional[int] = None
    any_release_ok: bool = False
    blocklisted_dedupe_keys: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class RuntimeContext:
    client_available: bool = True
    path_mapping_valid: bool = True
    staging_available: bool = True
    free_space_bytes: Optional[int] = None
    active_dedupe_keys: frozenset[str] = field(default_factory=frozenset)
    current_quality_rank: Optional[int] = None
    expected_size_bytes: Optional[int] = None


@dataclass(frozen=True)
class EffectivePolicy:
    quality_targets: Tuple[QualityTarget, ...] = field(default_factory=tuple)
    fallback_enabled: bool = True
    cutoff_index: Optional[int] = None
    custom_format_scores: Mapping[str, int] = field(default_factory=dict)
    required_custom_formats: frozenset[str] = field(default_factory=frozenset)
    blocked_custom_formats: frozenset[str] = field(default_factory=frozenset)
    minimum_custom_format_score: Optional[int] = None
    allowed_languages: frozenset[str] = field(default_factory=frozenset)
    allowed_release_types: frozenset[str] = field(default_factory=frozenset)
    min_size_bytes: Optional[int] = None
    max_size_bytes: Optional[int] = None
    minimum_seeders: int = 0
    minimum_age_seconds: int = 0
    maximum_age_seconds: Optional[int] = None
    protocol_priorities: Mapping[str, int] = field(default_factory=dict)
    source_priorities: Mapping[str, int] = field(default_factory=dict)
    source_policy: Optional[SourcePolicy] = None

    @classmethod
    def from_profile(
        cls, profile: Mapping[str, Any], **overrides: Any,
    ) -> "EffectivePolicy":
        profile_value = dict(profile)
        ranked = profile_value.get("ranked_targets")
        if isinstance(ranked, str):
            try:
                profile_value["ranked_targets"] = json.loads(ranked)
            except (TypeError, ValueError):
                profile_value["ranked_targets"] = []
        targets, fallback = targets_from_profile(profile_value)
        upgrade_policy = str(profile_value.get("upgrade_policy") or "acceptable")
        cutoff = None
        if upgrade_policy in {"until_cutoff", "until_top"} and targets:
            cutoff = 0 if upgrade_policy == "until_top" else int(
                profile_value.get("upgrade_cutoff_index") or 0)
            cutoff = max(0, min(cutoff, len(targets) - 1))
        values: Dict[str, Any] = {
            "quality_targets": tuple(targets),
            "fallback_enabled": bool(fallback),
            "cutoff_index": cutoff,
        }
        values.update(overrides)
        return cls(**values)


@dataclass(frozen=True)
class CandidateDecision:
    request_id: str
    candidate_id: str
    accepted: bool
    forced: bool
    reasons: Tuple[DecisionReason, ...]
    quality_rank: int
    cutoff_delta: Optional[int]
    custom_format_score: int
    edition_match_confidence: float
    sort_key: Tuple[float, ...]
    engine_version: str = ENGINE_VERSION

    @property
    def rejections(self) -> Tuple[DecisionReason, ...]:
        return tuple(reason for reason in self.reasons if reason.severity == "rejection")

    @property
    def warnings(self) -> Tuple[DecisionReason, ...]:
        return tuple(reason for reason in self.reasons if reason.severity == "warning")

    @property
    def can_force(self) -> bool:
        return bool(self.rejections) and all(
            reason.overridable for reason in self.rejections)

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "candidate_id": self.candidate_id,
            "accepted": self.accepted,
            "forced": self.forced,
            "can_force": self.can_force,
            "rejections": [reason.to_dict() for reason in self.rejections],
            "warnings": [reason.to_dict() for reason in self.warnings],
            "quality_rank": self.quality_rank,
            "cutoff_delta": self.cutoff_delta,
            "custom_format_score": self.custom_format_score,
            "edition_match_confidence": self.edition_match_confidence,
            "sort_key": list(self.sort_key),
            "engine_version": self.engine_version,
        }


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _expected_content_scope(request: AcquisitionRequest) -> Optional[str]:
    explicit = str(request.search_options.get("content_scope") or "").strip().lower()
    if explicit in {"recording", "release_bundle"}:
        return explicit
    if request.scope == "recording":
        return "recording"
    if request.scope in {"release_group", "release_edition", "artist_missing"}:
        return "release_bundle"
    return None


def _quality(candidate: ReleaseCandidate) -> Optional[AudioQuality]:
    facts = candidate.facts
    if not facts.format:
        return None
    return AudioQuality(
        format=facts.format,
        bitrate=facts.bitrate,
        sample_rate=facts.sample_rate,
        bit_depth=facts.bit_depth,
    )


class DecisionEngine:
    """Pure evaluator. Persistence is handled by ``core.acquisition.decisions``."""

    @classmethod
    def evaluate(
        cls,
        request: AcquisitionRequest,
        candidate: ReleaseCandidate,
        catalog: CatalogContext,
        runtime: RuntimeContext,
        policy: EffectivePolicy,
        *,
        now: Optional[float] = None,
        force: bool = False,
        is_admin: bool = False,
    ) -> CandidateDecision:
        reasons: list[DecisionReason] = []

        def reject(specification: str, code: str, message: str,
                   *, overridable: bool = False) -> None:
            reasons.append(DecisionReason(
                specification, code, "rejection", message, overridable))

        def warn(specification: str, code: str, message: str) -> None:
            reasons.append(DecisionReason(
                specification, code, "warning", message, True))

        # Stage A: security and identity.
        if candidate.request_id != request.id:
            reject("request_ownership", "candidate_request_mismatch",
                   "Candidate belongs to a different acquisition request")
        if candidate.expires_at <= (time.time() if now is None else float(now)):
            reject("candidate_expiry", "candidate_expired", "Candidate has expired")
        if not candidate.server_ref:
            reject("server_reference", "server_reference_missing",
                   "Candidate has no server-side download reference")

        capabilities = get_source_capabilities(candidate.source)
        if capabilities is None:
            reject("source_capability", "source_capability_unknown",
                   "Download source has no declared capabilities")
        elif candidate.content_scope != capabilities.content_scope:
            reject("source_capability", "source_scope_invalid",
                   "Candidate scope conflicts with source capabilities")

        expected_scope = _expected_content_scope(request)
        if expected_scope and candidate.content_scope != expected_scope:
            reject("request_scope", "content_scope_mismatch",
                   f"Request expects {expected_scope}, candidate is {candidate.content_scope}")

        if catalog.artist:
            if not candidate.facts.artist:
                reject("artist_match", "artist_unknown",
                       "Candidate does not identify an artist", overridable=True)
            elif _normalize(candidate.facts.artist) != _normalize(catalog.artist):
                reject("artist_match", "artist_mismatch",
                       "Candidate artist does not match the requested artist")

        if catalog.release_title:
            if not candidate.facts.release_title:
                reject("release_match", "release_unknown",
                       "Candidate does not identify a release", overridable=True)
            elif _normalize(candidate.facts.release_title) != _normalize(catalog.release_title):
                reject("release_match", "release_mismatch",
                       "Candidate release does not match the requested release")

        edition_confidence = 0.0
        if catalog.edition:
            if candidate.facts.edition and (
                _normalize(candidate.facts.edition) == _normalize(catalog.edition)
            ):
                edition_confidence = 1.0
            elif catalog.any_release_ok:
                edition_confidence = 0.5
                warn("edition_match", "edition_not_exact",
                     "Candidate edition is not exact, but any edition is allowed")
            elif not candidate.facts.edition:
                reject("edition_match", "edition_unknown",
                       "Candidate does not identify the requested edition",
                       overridable=True)
            else:
                reject("edition_match", "edition_mismatch",
                       "Candidate edition differs from the requested edition",
                       overridable=True)
        elif candidate.facts.release_title:
            edition_confidence = 0.5

        if candidate.dedupe_key in catalog.blocklisted_dedupe_keys:
            reject("blocklist", "candidate_blocklisted",
                   "Candidate is present in the release blocklist")

        # Stage B: operational readiness.
        if not runtime.client_available:
            reject("client_health", "download_client_unavailable",
                   "Download client is unavailable")
        if not runtime.path_mapping_valid:
            reject("path_mapping", "path_mapping_invalid",
                   "Remote path mapping is invalid")
        if not runtime.staging_available:
            reject("staging", "staging_unavailable",
                   "Staging path is unavailable")
        if candidate.dedupe_key in runtime.active_dedupe_keys:
            reject("active_grab", "duplicate_active_grab",
                   "The same candidate already has an active grab")
        if (
            runtime.free_space_bytes is not None
            and candidate.size_bytes is not None
            and candidate.size_bytes > runtime.free_space_bytes
        ):
            reject("free_space", "insufficient_free_space",
                   "Candidate is larger than available staging space")

        # Stage C: profile policy.
        quality = _quality(candidate)
        target_count = len(policy.quality_targets)
        if quality is None:
            quality_rank = target_count
            if target_count:
                warn("quality", "quality_unknown",
                     "Candidate does not expose enough quality metadata")
        else:
            quality_rank, _tier = rank_candidate(
                quality, list(policy.quality_targets))
            if target_count and quality_rank >= target_count:
                if policy.fallback_enabled:
                    warn("quality", "quality_fallback",
                         "Candidate matches no ranked target and uses profile fallback")
                else:
                    reject("quality", "quality_not_allowed",
                           "Candidate matches no allowed quality target",
                           overridable=True)

        cutoff_delta = (
            quality_rank - policy.cutoff_index
            if policy.cutoff_index is not None else None
        )
        if cutoff_delta is not None and cutoff_delta > 0:
            warn("cutoff", "below_cutoff",
                 "Candidate is acceptable but remains below the upgrade cutoff")
        if (
            request.scope == "upgrade"
            and runtime.current_quality_rank is not None
            and quality_rank >= runtime.current_quality_rank
        ):
            reject("upgrade", "not_an_upgrade",
                   "Candidate does not improve the current file quality")

        candidate_formats = {
            _normalize(value) for value in candidate.facts.custom_formats}
        blocked_formats = {
            _normalize(value) for value in policy.blocked_custom_formats}
        forbidden = sorted(candidate_formats & blocked_formats)
        if forbidden:
            reject("custom_format", "custom_format_blocked",
                   f"Candidate contains blocked custom formats: {', '.join(forbidden)}")
        required_formats = {
            _normalize(value) for value in policy.required_custom_formats}
        missing_formats = sorted(required_formats - candidate_formats)
        if missing_formats:
            reject("custom_format", "custom_format_required",
                   f"Candidate misses required custom formats: {', '.join(missing_formats)}",
                   overridable=True)
        score_lookup = {
            _normalize(key): int(value)
            for key, value in policy.custom_format_scores.items()
        }
        custom_score = sum(score_lookup.get(value, 0) for value in candidate_formats)
        if (
            policy.minimum_custom_format_score is not None
            and custom_score < policy.minimum_custom_format_score
        ):
            reject("custom_format", "custom_format_score_too_low",
                   "Candidate custom-format score is below the configured minimum",
                   overridable=True)

        if policy.allowed_languages:
            language = _normalize(candidate.facts.language)
            allowed = {_normalize(value) for value in policy.allowed_languages}
            if not language:
                warn("language", "language_unknown", "Candidate language is unknown")
            elif language not in allowed:
                reject("language", "language_not_allowed",
                       "Candidate language is not allowed", overridable=True)
        if policy.allowed_release_types:
            release_type = _normalize(candidate.facts.release_type)
            allowed = {_normalize(value) for value in policy.allowed_release_types}
            if release_type and release_type not in allowed:
                reject("release_type", "release_type_not_allowed",
                       "Candidate release type is not allowed", overridable=True)

        if candidate.size_bytes is not None:
            if policy.min_size_bytes is not None and candidate.size_bytes < policy.min_size_bytes:
                reject("size", "size_too_small", "Candidate is below minimum size",
                       overridable=True)
            if policy.max_size_bytes is not None and candidate.size_bytes > policy.max_size_bytes:
                reject("size", "size_too_large", "Candidate exceeds maximum size",
                       overridable=True)
        elif policy.min_size_bytes is not None or policy.max_size_bytes is not None:
            warn("size", "size_unknown", "Candidate size is unknown")

        if candidate.protocol == "torrent" and policy.minimum_seeders > 0:
            if candidate.seeders is None:
                warn("availability", "seeders_unknown", "Torrent seeder count is unknown")
            elif candidate.seeders < policy.minimum_seeders:
                reject("availability", "not_enough_seeders",
                       "Torrent has fewer seeders than required", overridable=True)
        if candidate.protocol == "usenet":
            if candidate.age_seconds is not None:
                if candidate.age_seconds < policy.minimum_age_seconds:
                    reject("usenet_age", "usenet_too_new",
                           "Usenet release is younger than the propagation delay",
                           overridable=True)
                if (
                    policy.maximum_age_seconds is not None
                    and candidate.age_seconds > policy.maximum_age_seconds
                ):
                    reject("usenet_retention", "usenet_outside_retention",
                           "Usenet release exceeds configured retention")

        if catalog.track_count is not None:
            if candidate.facts.track_count is None:
                warn("track_count", "track_count_unknown",
                     "Candidate track count is unknown")
            elif candidate.facts.track_count != catalog.track_count:
                warn("track_count", "track_count_mismatch",
                     "Candidate track count differs from the selected edition")
            else:
                edition_confidence = min(1.0, edition_confidence + 0.25)

        # Stage D: deterministic ranking, independent of acceptance.
        protocol_priority = int(policy.protocol_priorities.get(candidate.protocol, 100))
        source_priority = int(policy.source_priorities.get(candidate.source, 100))
        availability = candidate.seeders if candidate.protocol == "torrent" else candidate.grabs
        availability = int(availability or 0)
        size_distance = (
            abs(candidate.size_bytes - runtime.expected_size_bytes)
            if candidate.size_bytes is not None and runtime.expected_size_bytes is not None
            else 0
        )
        quality_sort = (
            float(quality_rank),
            float(-custom_score),
            float(-edition_confidence),
        )
        source_sort = (
            float(protocol_priority),
            float(source_priority),
        )
        if policy.source_policy and not policy.source_policy.quality_first:
            sort_key = source_sort + quality_sort + (
                float(-availability),
                float(size_distance),
            )
        else:
            sort_key = quality_sort + source_sort + (
                float(-availability),
                float(size_distance),
            )

        rejections = [reason for reason in reasons if reason.severity == "rejection"]
        forced = False
        accepted = not rejections
        if force:
            if not is_admin:
                reasons.append(DecisionReason(
                    "force_grab", "force_requires_admin", "rejection",
                    "Force grab requires an administrator", False))
                accepted = False
            elif rejections and all(reason.overridable for reason in rejections):
                accepted = True
                forced = True

        return CandidateDecision(
            request_id=request.id,
            candidate_id=candidate.id,
            accepted=accepted,
            forced=forced,
            reasons=tuple(reasons),
            quality_rank=quality_rank,
            cutoff_delta=cutoff_delta,
            custom_format_score=custom_score,
            edition_match_confidence=edition_confidence,
            sort_key=sort_key,
        )


__all__ = [
    "ENGINE_VERSION",
    "CandidateDecision",
    "CatalogContext",
    "DecisionEngine",
    "DecisionReason",
    "EffectivePolicy",
    "RuntimeContext",
]

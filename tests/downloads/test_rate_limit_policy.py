"""Tests for the per-source RateLimitPolicy declaration mechanism (Phase E1)."""

from __future__ import annotations

from core.download_engine import DownloadEngine, RateLimitPolicy
from core.download_engine.rate_limit import DEFAULT_POLICY, resolve_policy


# ---------------------------------------------------------------------------
# resolve_policy
# ---------------------------------------------------------------------------


def test_resolve_policy_returns_default_when_plugin_declares_nothing():
    plugin = object()
    assert resolve_policy(plugin) is DEFAULT_POLICY


def test_resolve_policy_reads_class_attribute():
    class _Plugin:
        RATE_LIMIT_POLICY = RateLimitPolicy(download_delay_seconds=5.0)

    policy = resolve_policy(_Plugin())
    assert policy.download_delay_seconds == 5.0


def test_resolve_policy_prefers_method_over_class_attribute():
    class _Plugin:
        RATE_LIMIT_POLICY = RateLimitPolicy(download_delay_seconds=1.0)

        def rate_limit_policy(self):
            return RateLimitPolicy(download_delay_seconds=10.0)

    assert resolve_policy(_Plugin()).download_delay_seconds == 10.0


def test_resolve_policy_falls_back_to_default_when_method_returns_garbage():
    class _Plugin:
        def rate_limit_policy(self):
            return "not a policy object"

    assert resolve_policy(_Plugin()) is DEFAULT_POLICY


def test_resolve_policy_falls_back_to_default_when_method_raises():
    class _Plugin:
        def rate_limit_policy(self):
            raise RuntimeError("boom")

    assert resolve_policy(_Plugin()) is DEFAULT_POLICY


# ---------------------------------------------------------------------------
# Engine applies policy on register
# ---------------------------------------------------------------------------


def test_engine_applies_declared_policy_on_register():
    """Pinning: when a plugin is registered, its declared
    RateLimitPolicy is pushed into the worker's per-source semaphore +
    delay registry. Future dispatches use those values."""
    class _ThrottledPlugin:
        RATE_LIMIT_POLICY = RateLimitPolicy(download_concurrency=1, download_delay_seconds=2.5)

    engine = DownloadEngine()
    engine.register_plugin('throttled', _ThrottledPlugin())

    assert engine.worker._get_delay('throttled') == 2.5


def test_engine_applies_default_policy_when_plugin_declares_nothing():
    """Plugins without a declaration get the conservative default
    (concurrency=1, delay=0)."""
    class _DefaultPlugin:
        pass

    engine = DownloadEngine()
    engine.register_plugin('default', _DefaultPlugin())

    assert engine.worker._get_delay('default') == 0.0


def test_set_engine_callback_runs_after_policy_applied():
    """Pinning: set_engine fires AFTER policy registration, so
    config-driven sources can override their declared policy.
    YouTube uses this — set_engine reads the user-tunable
    youtube.download_delay config and overrides the declared default."""
    fired_at: list = []

    class _Plugin:
        RATE_LIMIT_POLICY = RateLimitPolicy(download_delay_seconds=1.0)

        def set_engine(self, engine):
            # Capture worker state at the moment set_engine fires.
            fired_at.append(engine.worker._get_delay('flexible'))
            # Then override.
            engine.worker.set_delay('flexible', 99.0)

    engine = DownloadEngine()
    engine.register_plugin('flexible', _Plugin())

    # The class-attribute value was applied first.
    assert fired_at == [1.0]
    # Then set_engine overrode it.
    assert engine.worker._get_delay('flexible') == 99.0

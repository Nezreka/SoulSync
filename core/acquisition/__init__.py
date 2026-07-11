"""Acquisition layer (audit Kap. 11.4, Phase 4).

Grows incrementally: ``grabs`` (ADR-07 persistent client correlation) first;
``acquisition_requests`` / ``release_candidates`` / decision engine follow.
"""

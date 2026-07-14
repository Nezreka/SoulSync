"""SoulSync video Collections — SoulSync-managed movie/show collections.

Kometa-parity feature that mirrors the overlay studio's shape: DB-backed
definitions, a pure resolver, and (later phases) a server-agnostic sync engine
with an incremental ledger, a full-bleed studio UI, and daily automation.

A definition resolves (per run) to a set of OWNED library items which is synced
to the active video server as a Plex Collection / Jellyfin BoxSet. Two builder
kinds:
  * ``smart`` — filter rules over the owned library (see :mod:`smart_filter`)
  * ``list``  — a TMDB franchise/list or Trakt list intersected with what's owned
"""

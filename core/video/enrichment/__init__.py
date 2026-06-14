"""SoulSync — isolated VIDEO enrichment subsystem.

Mirrors the music enrichment-worker pattern (per-source match status, a worker
loop, a registry) but operates entirely on video.db and never imports the music
enrichment code. The shared Manage-Workers modal / worker-orbs visuals are made
kind-aware separately; this package is the backend half.
"""

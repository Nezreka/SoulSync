"""Overlay compositing (Artwork Studio → apply side).

Turns a saved overlay template (the JSON scene authored in the Overlay Studio
editor) into a poster image with the badges/logos/shapes burned in, ready to push
to Plex/Jellyfin. The editor is the CREATE side; this package is the APPLY side.

Modules:
- fields: value → badge-text formatters (mirror the editor's JS formatters so what
  you designed is what renders).
- compositor: render_overlay(base_bytes, definition, values) -> JPEG bytes.
- assets: the per-item base/backup/output store under the data dir.
"""

# Notes for agents and contributors

Before changing anything under `core/library2/`, `core/repair_jobs/`,
`core/imports/`, `core/matching/` or the library pages, read
[docs/library-v2-data-flow.md](docs/library-v2-data-flow.md). In short:

- The `lib2_*` catalogue is the truth. Fetchers ingest external evidence,
  healers correct catalogue identities and relationships, projection writers
  (Retag, Reorganize/Rename) read the committed effective catalogue and write
  files or paths. A healer never implements a tag codec or file mover.
- A healer finds its subjects in the catalogue, not in files. Missing
  evidence: it requests the existing scoped fetcher, then reads again. It
  heals only the database, commits, and may hand the changed tracks to the
  projection jobs (`RepairWorker.request_projection`), which act by their own
  settings. It never runs a tag write or a move itself.
- Fix the cause in its owning layer, not with another caller-specific rule.
- One competence, one owner: a job that needs another's work asks for it,
  scoped (`request_projection` for Re-tag / Reorganize, `request_split` for
  Split Artists, the only job that splits an artist).
- One question, one kernel: search for the existing function before writing a
  helper (artist credits: `core/matching/artist_credit.py`; paths:
  `core/imports/paths.build_final_path_for_track`; user-visible values:
  `core/library2/metadata_overrides.project_metadata`).
- Evidence before names, lossless default: nothing is split, renamed or
  merged on a guess.
- A reported case gets a test in that kernel's corpus.

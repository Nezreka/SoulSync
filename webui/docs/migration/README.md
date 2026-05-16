# WebUI Migration Docs

This folder is the home for React migration planning work inside `webui`.

## Purpose

- Keep migration planning close to the code it describes.
- Separate WebUI migration docs from repo-level product or backend docs.
- Give each route migration a predictable place to live.

## Current Docs

- [page-migration-overview.md](./page-migration-overview.md)
  - high-level route inventory
  - migration waves
  - cross-route risk assessment
- [stats-migration-plan.md](./stats-migration-plan.md)
  - route-specific migration plan for `stats`

## Naming Guidance

- Keep one high-level backlog / sequencing doc:
  - `page-migration-overview.md`
- Use one route-specific plan per migration task:
  - `<route>-migration-plan.md`

Examples:

- `search-migration-plan.md`
- `watchlist-migration-plan.md`
- `library-migration-plan.md`

## Scope

Use this folder for:

- migration sequencing
- route-specific implementation sketches
- React ownership cutover notes
- shell handoff notes tied to WebUI page migrations

Do not use this folder for:

- generic product docs
- backend architecture notes unrelated to WebUI migration
- permanent user-facing documentation

# Library v2 quality upgrades implementation plan

> **For agentic workers:** Use superpowers:executing-plans; implement and verify each task in this isolated checkout.

**Goal:** Preserve the single Library-v2 acquisition path while restoring optional quality review and adapting upstream quality automation and presentation.

**Architecture:** Reuse native file subjects, live profile inheritance, the wanted projection and its outbox. The audit is opt-in and only emits findings; applying an upgrade explicitly opts that track into monitoring. Scheduled applications only act on tracks already wanted, and never on unknown audio or untargeted formats. Consolidate physical-file replacement decisions behind one probed verdict.

**Tech Stack:** Python 3.11, SQLite, pytest, React/TypeScript, Vitest.

**Spec:** Approved in-chat recommendation and implementation request, 2026-10-03.

## Global constraints

- Preserve the original checkout, index and paused MERGE_HEAD byte-for-byte; commit only changes against the isolated snapshot.
- Never restore the retired upgrade jobs or add a second automatic wishlist writer.
- Keep owner-library scope, primary-file identity, retention provenance and sealed upgrade intents.
- No downloads, external API calls, real configuration or live databases in verification.

## Review focus

- An unmonitored file is audited but never auto-enrolled by automation.
- A live profile edit or primary-file replacement invalidates the pending proposal.
- Shared and own libraries keep independent file and wishlist ownership.
- Unknown quality and untargeted formats never default to destructive replacement.
- Stop/pause and explicit empty file scopes never claim a complete library-wide audit.

## Task 1: One measured replacement verdict

Files: core/library2/quality_eval.py, core/imports/pipeline.py, tests/library2/test_upgrade_verdict.py.
Interface: decide_probed_upgrade(old_quality, new_quality, profile, *, track_id, existing_path=None, existing_resolved_path=None, acquired_quality_json=None, retention_json=None) -> UpgradeDecision.

- [x] Add parameterized tests for bitrate improvements, equal/worse quality, cutoff completion, intentional retention and missing measurements.
- [x] Run pytest tests/library2/test_upgrade_verdict.py and confirm failure before implementing.
- [x] Move the existing profile-ranked comparison into decide_probed_upgrade; call it from catalogue and import-snapshot consumers.
- [x] Run the verdict tests and existing quality/import/owner-library regression suites.

## Task 2: Native audit and review application

Files: core/repair_jobs/quality_profile_audit.py, core/repair_jobs/__init__.py, core/repair_worker.py, core/library2/quality_eval.py, core/quality/upgrades.py, tests/repair_jobs/test_quality_profile_audit.py.
Interfaces: quality_issue(file_row, profile) -> str; audit job emits quality_upgrade_review, quality_format_not_targeted and quality_unknown findings with native track/file/owner ids.

- [x] Write tests using a real native SQLite catalogue and generated audio files for scan-only behavior, inherited profiles, file scopes, unknown/untargeted audio, retention, dismissal and stale proposals.
- [x] Run the new tests and confirm the missing audit/verdict behavior.
- [x] Register an opt-in weekly audit; enumerate primary files per track and owner, apply scopes and hand-tag/manual-skip guards, probe real audio and emit findings only.
- [x] Approving quality_upgrade_review revalidates the live primary file/profile and opts that one track into monitoring through the existing wanted/outbox transaction; other issue types default to ignored.
- [x] Adapt until_cutoff_finding_ids to native review findings with live profile, owner, wanted and file-id validation; preserve old findings as compatibility.
- [x] Run native audit, repair registry, recurrence, migration and automation tests.

## Task 3: Native presentation and verification

Files: core/library2/queries.py, core/automation/blocks.py, webui/src/routes/library/-library-v2.types.ts, webui/src/routes/library/-ui/library-v2-page.tsx, webui/src/routes/tools/-tools.core.ts, webui/src/routes/tools/-tools.groups.ts, tests/quality/test_quality_upgrades_surfaced.py and associated Vitest files.

- [x] Add regressions for format-not-targeted presentation, native cutoff selection and actual audit-to-automation-to-wishlist behavior.
- [x] Port the upstream test fixtures off removed legacy tables and remove obsolete findings-driven library-filter tests; retain native view coverage.
- [x] Present format mismatch separately from insufficient audio quality; label review approval Monitor & Upgrade and correct automation descriptions.
- [x] Run focused Python and UI tests, Python lint and UI checks. Run the project chunked Python suite and report pre-existing failures separately.
- [x] Review the complete diff against the private snapshot, commit only that delta and produce a patch/commit handoff without finishing the original merge.

## Final review corrections

Four Important integration findings were reproduced through public endpoints and
public `fix_finding`/bulk fix, then corrected in one final test-driven pass:

- Audit synchronization records the result without a second rescan/admin mirror.
- Scheduled owner provenance reaches the fixer, which rechecks live monitoring
  and cutoff policy in the writer transaction and never enrolls an unwanted track.
- The enhanced artist adapter keeps its helper, now backed by native profiles.
- Explicit approval uses user-initiated re-add semantics and verifies the actual
  Wishlist entry rather than treating a skipped outbox add as a queued upgrade.

Verification limits: the project-wide chunked run was interrupted after hanging
in the unrelated download chunk. A broader affected run passed 4,161 tests and
failed 19; all 19 failures reproduced on the unchanged paused-merge snapshot.
Final affected-directory verification runs in separate processes with exactly
those baseline failures deselected. Original live checkout remains untouched.

Final results: 4,661 affected Python tests passed in independent processes,
19 reproduced baseline failures deselected, 24 existing ownership regressions
skipped by their fixture. Full UI: 9,969 passed; production build succeeded;
changed Python files pass Ruff; UI check reports 517 warnings and 0 errors.

## Authorized integration into the normal worktree

After the other chat completed merge `b20e91205`, the user explicitly requested
integration. Only the feature delta from isolated commit `c1dfe258e` was
cherry-picked; the private snapshot commit was not integrated. Conflicts in
the action list and enhanced endpoint retain native behavior; the deleted
legacy test file is reintroduced only in its ported, native-schema form. The
other merge's batch ownership, AcoustID album-artist and lossless-source
retention corrections remain intact. Exactly the feature's 26 files change.

Fresh integration verification: 1,166 Python quality/import/repair/automation
regressions passed without baseline deselections; all 9,975 UI tests passed.
Changed Python files pass Ruff; UI check reports 517 warnings and 0 errors.
This is a targeted Python integration check, not another complete repository
Python run. No push or deployment was requested or performed.

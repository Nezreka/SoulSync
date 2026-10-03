# Quality upgrades in Library v2

Monitored tracks use the native wanted projection and mirror outbox for missing
files and quality upgrades. Monitoring List Reconcile remains responsible for
recovering that intent. The retired quality-upgrade jobs are not restored.

This native follow-up to the 3.5.0 merge enables the audit application action
and live-profile badges that the merge initially omitted as legacy-only. The
ported contract tests use the native schema; their legacy-table fixtures stay
removed. The merge's other catalogue, import and repair corrections are retained.

## Optional quality review

Enable **Quality Profile Audit** in Library Maintenance to review primary audio
files, including unmonitored tracks. It is disabled by default, has a weekly
interval, and never automatically fixes its findings. A scan measures audio and
creates findings without changing files, stored measurements, monitoring or the
Wishlist. Existing monitored-track automation continues independently.

- **Quality Upgrade Review**: the file is below its inherited upgrade target.
  **Monitor & Upgrade** rechecks the current file and profile, stores the fresh
  audio measurements, opts that one track into monitoring, and queues through
  the native wanted/outbox pipeline. This explicit re-add clears a previous
  cancellation's ignore entry. An acquisition exclusion that still prevents
  queuing leaves the finding pending with an error.
- **Format Not in Profile**: the profile does not target this audio format.
  **Leave As-is** resolves the finding without queuing a replacement. Review the
  profile's formats if replacement is intended.
- **Quality Unknown**: the audio could not be measured. **Leave As-is** does not
  queue a replacement; inspect the file before choosing another acquisition.

The audit respects explicit file scopes, hand-tagged releases, active manual
quality overrides and intentional retention provenance. It selects the same
primary file as acquisition within each owner's library. Approval fails if the
reviewed file or its owner changed; rerun the audit in that case. A profile edit
is evaluated live before approval. Dismissals survive repeat scans until the
finding's measured facts or profile change.

## Scheduled application

The **Apply Quality Upgrades** automation can apply native audit findings only
for already-wanted tracks in the automation owner's library whose live profile
uses **upgrade until cutoff**. Enable and run the audit first. Unmonitored
findings remain available for an explicit **Monitor & Upgrade** decision.
Untargeted formats and unknown quality are excluded from scheduled application.
Scheduled application rechecks monitoring, cutoff policy and owner in the
approval transaction; an intervening unmonitor/profile edit leaves the proposal
for manual review. It never clears the cancellation ignore list.
Preserved legacy findings remain readable for compatibility; they do not revive
a legacy scan job.

Library-v2 badges evaluate the current inherited profile independently of audit
findings. A format outside the profile has a separate badge from low quality.
The importer still measures old and incoming files and applies one shared
replacement verdict, including cutoff and retained acquisition provenance.

"""Persistence ownership and migration inventory for the review subsystem."""

ALBUM_ID_STORES = (
    'reconcile_album_intent', 'reconcile_wanted_slots', 'reconcile_release_decisions',
    'reconcile_track_assignment', 'reconcile_fulfillment', 'reconcile_album_plans',
    'reconcile_audio_copy_releases',
)
DOCUMENT_STORES = (
    ('repair_findings', 'id', 'details_json', 'review_case_key'),
    ('reconcile_review_choices', 'finding_id', 'evidence_json', 'case_id'),
    ('reconcile_replacements', 'replacement_id', 'details_json', 'case_id'),
)

# Specialized identity/revision stores are migrated through the decision repository.
STORE_OWNERS = {
    'reconcile_album_intent': 'album decisions',
    'reconcile_wanted_slots': 'album decisions',
    'reconcile_release_decisions': 'album decisions',
    'reconcile_audio_copy_releases': 'album decisions',
    'reconcile_reviews': 'review identity',
    'reconcile_review_aliases': 'review identity',
    'reconcile_album_decisions': 'album decisions',
    'reconcile_track_assignment': 'track decisions',
    'reconcile_review_choices': 'track decisions',
    'reconcile_saved_plans': 'apply approval',
    'reconcile_fulfillment': 'acquisition',
    'reconcile_replacements': 'replacement operations',
    'reconcile_album_plans': 'evidence snapshots',
    'reconcile_case_redirects': 'review identity',
    'reconcile_schema_versions': 'schema migrations',
    'repair_operation_reports': 'operation history',
    'repair_findings': 'proposals and findings history',
    'reconcile_file_fingerprints': 'evidence cache',
    'reconcile_acoustid_results': 'evidence cache',
    'reconcile_file_tags': 'evidence cache',
    'reconcile_catalogue_cache': 'evidence cache',
    'reconcile_match_cache': 'evidence cache',
    'reconcile_scan_metrics': 'scan diagnostics',
}

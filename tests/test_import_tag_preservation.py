"""Regression: a metadata-enhancement failure must NOT wipe a clean/matched
import's tags (#804 — already-tagged files were blanked into Unknown Artist).
"""

from __future__ import annotations

from core.imports.tag_policy import should_wipe_tags_on_enhancement_failure


def test_clean_matched_import_is_never_wiped_on_failure():
    # The #804 case: matched import (clean metadata) → preserve existing tags.
    assert should_wipe_tags_on_enhancement_failure(has_clean_metadata=True) is False


def test_unmatched_download_still_strips_junk_on_failure():
    # Unchanged behavior for unmatched downloads (likely junk source tags).
    assert should_wipe_tags_on_enhancement_failure(has_clean_metadata=False) is True


def test_falsey_values_treated_as_unmatched():
    assert should_wipe_tags_on_enhancement_failure(None) is True
    assert should_wipe_tags_on_enhancement_failure(0) is True

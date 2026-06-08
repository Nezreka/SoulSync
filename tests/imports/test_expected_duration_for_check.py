"""Duration-agreement leg is skipped for local/manual imports (#804).

The duration check catches truncated/wrong slskd downloads. A manual import is
the user's own file being sorted — duration-agreeing it against a re-resolved
release false-quarantines (Coldplay 'Yellow' album file vs a single's length).
"""

from core.imports.file_integrity import expected_duration_for_check


def test_local_import_skips_duration_leg():
    # Even with a valid expected duration, a local import returns None (skip).
    assert expected_duration_for_check(266000, is_local_import=True) is None


def test_download_keeps_expected_duration():
    assert expected_duration_for_check(266000, is_local_import=False) == 266000


def test_zero_or_missing_expected_is_none():
    assert expected_duration_for_check(0, is_local_import=False) is None
    assert expected_duration_for_check(None, is_local_import=False) is None
    assert expected_duration_for_check("nan", is_local_import=False) is None


def test_string_numeric_expected_coerced():
    assert expected_duration_for_check("266000", is_local_import=False) == 266000

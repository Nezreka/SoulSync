"""Date/time comparison bugs in the "fix false-positive date retag
warnings" change (commit 73ec9c7a):

- ``_normalize_date_str`` only matched strings that included a
  time-of-day component, so a bare ``YYYY-MM-DD`` never normalized
  equal to the same date with a ``T00:00:00`` suffix — defeating the
  false-positive suppression for the single most common shape of this
  mismatch (a full ISO timestamp on the file vs. a plain date in the DB).
- ``_date_to_write`` used string length as a proxy for "more specific,"
  so a longer but genuinely WRONG existing date (same year, different
  month/day) could permanently block a real correction from a shorter
  but correct new value.
"""

from __future__ import annotations

from core.tag_writer import _date_to_write, _normalize_date_str, build_tag_diff


# ---------------------------------------------------------------------------
# _normalize_date_str
# ---------------------------------------------------------------------------


def test_normalize_bare_date_matches_same_date_with_midnight_timestamp():
    assert _normalize_date_str('2023-09-01') == _normalize_date_str('2023-09-01T00:00:00')


def test_normalize_bare_date_matches_same_date_with_zulu_midnight_timestamp():
    assert _normalize_date_str('2023-09-01') == _normalize_date_str('2023-09-01T00:00:00Z')


def test_normalize_still_distinguishes_different_dates():
    assert _normalize_date_str('2023-09-01') != _normalize_date_str('2023-09-05')
    assert _normalize_date_str('2023-01-01T00:00:00') != _normalize_date_str('2023-09-05')


def test_normalize_empty_string_is_empty():
    assert _normalize_date_str('') == ''


# ---------------------------------------------------------------------------
# _date_to_write
# ---------------------------------------------------------------------------


def test_date_to_write_applies_a_genuine_correction_within_the_same_year():
    """A longer existing tag must not block a real month/day correction
    just because it happens to be a longer string."""
    assert _date_to_write('2023-01-01 00:00:00', '2023-09-05') == '2023-09-05'


def test_date_to_write_applies_a_genuine_correction_across_years():
    assert _date_to_write('2019-12-31T23:00:00+00:00', '2020-01-01') == '2020-01-01'


def test_date_to_write_keeps_existing_full_date_when_new_value_is_a_bare_year():
    """#824: enrichment must not downgrade a real full release date to
    just the year when the new source only provides a bare year."""
    assert _date_to_write('2020-03-15', 2020) == '2020-03-15'
    assert _date_to_write('2020-03-15', '2020') == '2020-03-15'


def test_date_to_write_writes_the_bare_year_when_years_differ():
    assert _date_to_write('2019-03-15', 2020) == '2020'


def test_date_to_write_recognizes_format_only_differences_as_unchanged():
    """A file timestamp and a plain DB date for the SAME real date must
    both resolve to keeping the (more informative) existing value."""
    assert _date_to_write('2023-09-01T00:00:00', '2023-09-01') == '2023-09-01T00:00:00'


def test_date_to_write_with_no_existing_value_uses_the_new_year():
    assert _date_to_write(None, '2023-09-05') == '2023-09-05'
    assert _date_to_write('', '2023-09-05') == '2023-09-05'


# ---------------------------------------------------------------------------
# build_tag_diff — integration: the year-diff suppression must not leave a
# spurious "changed" diff for the bare-date-vs-timestamp shape.
# ---------------------------------------------------------------------------


def test_build_tag_diff_suppresses_bare_date_vs_midnight_timestamp_false_positive():
    file_tags = {'year': '2023-09-01T00:00:00'}
    db_data = {'year': 2023, 'release_date': '2023-09-01'}
    diffs = build_tag_diff(file_tags, db_data)
    year_diff = next(d for d in diffs if d['field'] == 'Year')
    assert year_diff['changed'] is False


def test_build_tag_diff_still_flags_a_genuine_year_difference():
    file_tags = {'year': '2023-01-01T00:00:00'}
    db_data = {'year': 2023, 'release_date': '2023-09-05'}
    diffs = build_tag_diff(file_tags, db_data)
    year_diff = next(d for d in diffs if d['field'] == 'Year')
    assert year_diff['changed'] is True

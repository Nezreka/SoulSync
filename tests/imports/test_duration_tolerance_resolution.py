from core.imports.file_integrity import _MAX_USER_TOLERANCE_S, resolve_duration_tolerance


def test_none_returns_none_so_caller_uses_auto_scaled_default():
    assert resolve_duration_tolerance(None) is None


def test_missing_or_empty_string_returns_none():
    assert resolve_duration_tolerance("") is None
    assert resolve_duration_tolerance("   ") is None


def test_zero_returns_none_to_avoid_strict_mode_ambiguity():
    # 0 means "unset" — never strict-mode (which would fail any drift).
    # Users who want strict have no use-case; users who want disabled
    # set a high value (capped to _MAX_USER_TOLERANCE_S).
    assert resolve_duration_tolerance(0) is None
    assert resolve_duration_tolerance(0.0) is None
    assert resolve_duration_tolerance("0") is None


def test_negative_returns_none():
    assert resolve_duration_tolerance(-1) is None
    assert resolve_duration_tolerance(-3.5) is None
    assert resolve_duration_tolerance("-10") is None


def test_positive_integer_passes_through_as_float():
    assert resolve_duration_tolerance(5) == 5.0
    assert resolve_duration_tolerance(10) == 10.0


def test_positive_float_passes_through():
    assert resolve_duration_tolerance(3.5) == 3.5
    assert resolve_duration_tolerance(0.1) == 0.1


def test_numeric_string_parsed():
    assert resolve_duration_tolerance("5") == 5.0
    assert resolve_duration_tolerance("3.5") == 3.5
    assert resolve_duration_tolerance("10.0") == 10.0


def test_unparseable_string_returns_none():
    assert resolve_duration_tolerance("abc") is None
    assert resolve_duration_tolerance("five") is None
    assert resolve_duration_tolerance("3s") is None


def test_above_max_clamped_to_ceiling():
    assert resolve_duration_tolerance(9999) == _MAX_USER_TOLERANCE_S
    assert resolve_duration_tolerance(_MAX_USER_TOLERANCE_S + 1) == _MAX_USER_TOLERANCE_S


def test_at_ceiling_passes_through():
    assert resolve_duration_tolerance(_MAX_USER_TOLERANCE_S) == _MAX_USER_TOLERANCE_S


def test_non_numeric_types_return_none():
    assert resolve_duration_tolerance([5]) is None
    assert resolve_duration_tolerance({"value": 5}) is None
    assert resolve_duration_tolerance(object()) is None


def test_bool_treated_as_int_python_semantics():
    # Python: bool is int subclass. True -> 1.0, False -> 0 -> None.
    # Documented behavior, not a bug — config values won't realistically
    # be booleans for a numeric setting.
    assert resolve_duration_tolerance(True) == 1.0
    assert resolve_duration_tolerance(False) is None

from core.library2.feature import coerce_bool, library_v2_enabled


def test_boolean_config_shapes_are_normalized():
    assert coerce_bool("true") is True
    assert coerce_bool("0") is False
    assert coerce_bool(1) is True
    assert coerce_bool(0) is False


def test_library_v2_cutover_is_not_disableable():
    assert library_v2_enabled(config_get=lambda _key, _default=None: False) is True
    assert library_v2_enabled(config_get=lambda _key, _default=None: "false") is True

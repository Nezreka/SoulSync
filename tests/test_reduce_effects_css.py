"""Guard the reduce-visual-effects CSS contract.

Performance mode must kill the GPU-EXPENSIVE properties (backdrop-filter / box-shadow
/ filter — the real lag, esp. on Firefox) but must NOT blanket-freeze animations:
`animation: none` on every element stuck the dash-header worker-service spinners
mid-rotation, which read as "broken" (Discord/Boulder). Pins the surgical rule so a
future edit can't silently re-freeze the functional spinners or kill cheap hover
feedback."""

import re
from pathlib import Path

_STYLE = Path(__file__).resolve().parent.parent / "webui" / "static" / "style.css"


def _reduce_effects_global_body() -> str:
    css = _STYLE.read_text(encoding="utf-8")
    m = re.search(r"body\.reduce-effects \*,.*?\{([^}]*)\}", css, re.DOTALL)
    assert m, "global 'body.reduce-effects *' rule not found"
    return m.group(1)


def test_reduce_effects_still_kills_expensive_gpu_properties():
    body = _reduce_effects_global_body()
    for prop in ("backdrop-filter: none", "box-shadow: none", "filter: none"):
        assert prop in body, f"reduce-effects must still force {prop} (the real lag source)"


def test_reduce_effects_does_not_blanket_freeze_animations():
    body = _reduce_effects_global_body()
    # `animation: none` on * froze the dash-header worker-service spinners mid-spin;
    # cheap transform/opacity motion must survive so a spinner still reads as "working".
    assert "animation: none" not in body, (
        "blanket 'animation: none' re-freezes the worker spinners — keep motion alive, "
        "the expensive properties are already neutralized above"
    )
    assert "transition-duration: 0s" not in body, (
        "blanket 'transition-duration: 0s' kills the cheap Quick Actions hover feedback"
    )

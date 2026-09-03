"""Content must not depend on an animation to become visible.

#1209 (wishx): the "Multiple Sources Found" options were laid out, hoverable
and clickable but invisible. #1201: similar-artist bubbles the same. Both were
written as "start at opacity 0, fade in" — and Max Performance mode sets
``animation: none !important`` on everything, so the fade never ran and they
stayed at zero forever.

The working pattern is already in this stylesheet: .server-pl-card has no base
``opacity: 0`` and uses a ``both`` fill, so the fade still happens and killing
the animation leaves the element at its normal opacity. That is what this
pins.

DECORATIVE animations are exempt on purpose. A shimmer or sweep that loops
forever (``infinite``, no fill mode) is *supposed* to be invisible when it
isn't running — freezing one mid-sweep would look broken. The rule here is
narrow: a run-once fade (``forwards`` / ``both``) may not be the only thing
standing between the user and the content.
"""

from __future__ import annotations

import re
from pathlib import Path

_CSS = Path(__file__).resolve().parents[1] / "webui" / "static" / "style.css"

_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_BASE_OPACITY_ZERO = re.compile(r"(^|[;\s])opacity:\s*0\s*;")
_ANIMATION = re.compile(r"animation:\s*([^;]+);")


def _offenders() -> list[tuple[str, str]]:
    src = _CSS.read_text(encoding="utf-8", errors="replace")
    bad = []
    for match in _RULE.finditer(src):
        selector, body = match.group(1).strip(), match.group(2)
        if selector.startswith("@") or "keyframes" in selector:
            continue
        if not _BASE_OPACITY_ZERO.search(body):
            continue
        anim = _ANIMATION.search(body)
        if not anim:
            continue
        shorthand = " ".join(anim.group(1).split())
        # Run-once fades only. An infinite loop with no fill mode is a
        # decorative effect and is allowed to vanish when it stops.
        if "forwards" not in shorthand and "both" not in shorthand:
            continue
        name = selector.split("\n")[-1].strip()
        bad.append((name, shorthand))
    return bad


def test_no_content_is_invisible_without_its_animation():
    offenders = _offenders()
    assert not offenders, (
        "These rules sit at opacity 0 and only reach visibility through a "
        "run-once animation, so they render as blank-but-clickable whenever "
        "animations are disabled (Max Performance mode does exactly that):\n"
        + "\n".join(f"  {sel}  ->  animation: {anim}" for sel, anim in offenders)
        + "\n\nGive the element its resting opacity in the base rule and let the "
          "animation supply the fade (see .server-pl-card)."
    )

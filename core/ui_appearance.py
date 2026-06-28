"""Pure decisions for UI-appearance defaults.

Kept here (importable, no Flask/config coupling) so the rules are unit-testable in
isolation; web_server does only the request/config plumbing around them.

Worker orbs are a blurred 60fps canvas — the main remaining Firefox lag source after
the #935 sweep. So for a FIRST-TIME user (no saved preference) we default them OFF on
Firefox and ON everywhere else: a smooth first impression where it's needed, full
polish where the browser handles it. An explicit saved choice ALWAYS wins — this only
picks the default when the user hasn't chosen.
"""

from __future__ import annotations

from typing import Optional


def is_firefox_user_agent(user_agent: Optional[str]) -> bool:
    """True when a User-Agent string is Firefox.

    Used ONLY to pick a performance-friendly default — never to gate functionality —
    so a spoofed or unusual UA simply gets the default and the user can toggle. Chrome,
    Edge, Safari, Opera, Brave do not carry 'firefox' in their UA; Firefox does
    ('… Gecko/… Firefox/<ver>')."""
    return 'firefox' in str(user_agent or '').lower()


def resolve_worker_orbs_default(explicit: object, is_firefox: bool) -> bool:
    """Whether worker orbs should be on.

    ``explicit`` is the saved config value: ``True``/``False`` when the user has chosen,
    ``None`` when unset. An explicit choice always wins; when unset, default OFF on
    Firefox (perf) and ON elsewhere.
    """
    if explicit is None:
        return not is_firefox
    return explicit is not False


__all__ = ['is_firefox_user_agent', 'resolve_worker_orbs_default']

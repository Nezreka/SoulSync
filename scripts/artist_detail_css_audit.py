#!/usr/bin/env python3
"""CSS scoped to the vanilla page container must also reach the React page.

The React artist-detail page renders inside #webui-react-root, not inside
#artist-detail-page. Any rule written against the old container silently stops
applying — no error, no failing test, jsdom cannot see it at all. That is how a
SOURCE artist ended up offering Artist Radio and Enhance Quality: the rules that
hid them were scoped to a container the page no longer lives in.

Every such rule must name the React page's own hook (.artist-detail-page) too.
"""
import re, sys
from pathlib import Path

ROOT = Path("/mnt/e/Broque Projects/Github Projects/SoulSync")
css = (ROOT / "webui/static/style.css").read_text(encoding="utf-8", errors="replace")
page = (ROOT / "webui/src/routes/artist-detail/-ui/artist-detail-page.tsx").read_text(
    encoding="utf-8"
)

REACT_HOOK = "artist-detail-page"

if f'className="{REACT_HOOK}"' not in page:
    print("CSS AUDIT — issues:")
    print(f"   the page component no longer renders className=\"{REACT_HOOK}\";")
    print("   every rule scoped to the artist-detail container is now dead")
    sys.exit(1)

problems = []
for match in re.finditer(r"[^}]*#artist-detail-page[^{]*\{", css):
    selector = " ".join(match.group(0).rstrip("{").split())
    if f".{REACT_HOOK}" in selector:
        continue
    problems.append(selector[:150])

if problems:
    print("CSS AUDIT — issues:")
    print(f"  {len(problems)} rule(s) scoped to #artist-detail-page that the React page cannot match:")
    for selector in problems:
        print("   ", selector)
    sys.exit(1)

total = css.count("#artist-detail-page")
print("CSS AUDIT — clean")
print(f"  ({total} selector(s) scoped to the artist-detail container, all reaching the React page)")

#!/usr/bin/env python3
"""Every element the vanilla artist-detail page rendered must exist in the port.

The parity audit checks LOGIC — which fields the vanilla reads and which
cross-file functions it calls. It cannot see a whole section that was simply
never rendered, which is how the Back button and the entire Similar Artists
block went missing: no test asked for them, so nothing failed.

This walks the vanilla `#artist-detail-page` subtree in index.html and requires
every id and every class to appear somewhere in the React port.
"""
import re, sys
from pathlib import Path

ROOT = Path("/mnt/e/Broque Projects/Github Projects/SoulSync")
html = (ROOT / "webui/index.html").read_text(encoding="utf-8")

start = html.index('<div class="page" id="artist-detail-page">')
depth, end = 0, start
for m in re.finditer(r"<(/?)div\b[^>]*>", html[start:]):
    depth += 1 if m.group(1) == "" else -1
    if depth == 0:
        end = start + m.end()
        break
page = html[start:end]

port = "\n".join(
    p.read_text(encoding="utf-8")
    for p in (ROOT / "webui/src/routes/artist-detail").rglob("*.ts*")
    if ".test." not in p.name
)

# Ids the port builds from a template literal rather than a string constant.
TEMPLATED = re.compile(r"^(albums|eps|singles)-(section|grid|stats|completion-fill|owned-count|missing-count)$")

# Rendered by the vanilla page only, with a reason.
ALLOWED_MISSING = {
    "artist-detail-page": "the React host IS the page container",
    "artist-detail-retry-btn": "the retry button is wired by onClick, not by id lookup",
}

problems = []
for el_id in sorted(set(re.findall(r'id="([^"]+)"', page))):
    if el_id in ALLOWED_MISSING or TEMPLATED.match(el_id) or el_id in port:
        continue
    problems.append(f"id #{el_id} is rendered by the vanilla page but appears nowhere in the port")

for cls in sorted({c for a in re.findall(r'class="([^"]+)"', page) for c in a.split()}):
    if cls in ("hidden", "page") or cls in port:
        continue
    problems.append(f"class .{cls} is rendered by the vanilla page but appears nowhere in the port")

if problems:
    print("MARKUP AUDIT — issues:")
    for p in problems:
        print("  ", p)
    sys.exit(1)
print("MARKUP AUDIT — clean")
print(f"  (every id and class in the vanilla page is present in the port; "
      f"{len(ALLOWED_MISSING)} documented exceptions)")

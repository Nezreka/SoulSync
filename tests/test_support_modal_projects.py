"""The support modal lists Boulder's other projects (MetaSync, SoulSync Companion)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _modal():
    html = (ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    start = html.index('id="support-modal-overlay"')
    return html[start:html.index("<!-- Version Info Modal -->", start)]


def test_the_support_modal_links_the_other_projects():
    modal = _modal()
    assert 'class="support-projects"' in modal
    for url in ("https://github.com/Nezreka/MetaSync", "https://github.com/Nezreka/soulsync-extension"):
        link = re.search(r'<a href="%s"[^>]*>' % re.escape(url), modal)
        assert link, url
        assert 'target="_blank"' in link.group(0) and 'rel="noopener"' in link.group(0)


def test_every_project_class_is_styled():
    css = (ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")
    for cls in set(re.findall(r'class="(support-project[a-z-]*)"', _modal())):
        assert re.search(r"\.%s\s*[{:,]" % re.escape(cls), css), cls

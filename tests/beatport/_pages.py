"""real beatport pages, trimmed to 12 tracks a list.

captured oct 15 2026 through flaresolverr from beatport.com. only the
``__NEXT_DATA__`` queries are kept, so each fixture is the json beatport
ships, wrapped back into a page here.
"""

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures' / 'beatport'


def next_data(name):
    return json.loads((FIXTURES / f'{name}.json').read_text(encoding='utf-8'))


def page_html(name):
    """the fixture as the html page it came from"""
    body = (FIXTURES / f'{name}.json').read_text(encoding='utf-8')
    return (
        '<!DOCTYPE html><html><head><title>Beatport</title></head><body>'
        '<div id="__next"><a href="/track/decoy/1">Decoy</a></div>'
        f'<script id="__NEXT_DATA__" type="application/json">{body}</script>'
        '</body></html>'
    )


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.content = text.encode('utf-8')
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f'{self.status_code} for url')


class FakeScraper:
    """stands in for BeatportUnifiedScraper: serves fixture pages by url"""

    def __init__(self, pages):
        self.pages = dict(pages)
        self.calls = []

    def _get(self, url, timeout=None):
        self.calls.append(url)
        name = self.pages.get(url)
        if name is None:
            return FakeResponse('<html>not found</html>', 404)
        if name == 403:
            return FakeResponse('<html>cloudflare</html>', 403)
        return FakeResponse(page_html(name))

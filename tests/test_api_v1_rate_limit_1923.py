"""#1923: metasync's weekly report paged the library and hit RATE_LIMITED.

60 a minute per address was the only bucket. a valid api key now gets its
own, bigger one; no key or a bogus key keeps the old limit.
"""

import hashlib

import pytest
from flask import Flask

RAW_KEY = "sk_rate_limit_test_key"


class _Config:
    def get(self, key, default=None):
        if key == "api_keys":
            return [{"id": "k1", "label": "test", "key_prefix": RAW_KEY[:11],
                     "key_hash": hashlib.sha256(RAW_KEY.encode()).hexdigest()}]
        return default

    def set(self, *a, **kw):
        return None

    def save_config(self, *a, **kw):
        return None


@pytest.fixture
def client():
    import flask_limiter
    if not hasattr(flask_limiter, "__version__"):
        pytest.skip("another test stubbed flask_limiter")
    from api import create_api_blueprint, limiter

    app = Flask(__name__)
    limiter.init_app(app)
    app.register_blueprint(create_api_blueprint(), url_prefix="/api/v1")
    app.soulsync = {"config_manager": _Config()}
    limiter.reset()
    yield app.test_client()
    limiter.reset()


def _hit(client, n, key=None):
    headers = {"X-API-Key": key} if key else {}
    return [client.get("/api/v1/system/status", headers=headers).status_code for _ in range(n)]


def test_a_valid_key_can_page_past_sixty_a_minute(client):
    codes = _hit(client, 120, RAW_KEY)

    assert set(codes) == {200}


def test_no_key_keeps_the_old_limit(client):
    codes = _hit(client, 61)

    assert codes[:60] == [401] * 60 and codes[60] == 429


def test_a_bogus_key_does_not_earn_the_bigger_bucket(client):
    codes = _hit(client, 61, "sk_not_a_real_key")

    assert codes[:60] == [403] * 60 and codes[60] == 429


def test_the_keyed_bucket_is_separate_from_the_address(client):
    _hit(client, 60)  # spend the anonymous budget from this address

    assert _hit(client, 1, RAW_KEY) == [200]

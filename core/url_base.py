"""Explicit reverse-proxy mount path; independent of untrusted forwarded headers."""
import re


def normalize_url_base(value):
    value = str(value or "").strip().rstrip("/")
    if not value:
        return ""
    if not re.fullmatch(r"/(?:[A-Za-z0-9_-]+)(?:/[A-Za-z0-9_-]+)*", value):
        raise ValueError("SOULSYNC_URL_BASE must be a path such as /soulsync (no host, query or dot segments)")
    return value


class URLBaseMiddleware:
    """Accept proxies that preserve OR strip the configured prefix.

    Install outside Socket.IO so both HTTP and its upgrade/polling endpoint see
    the same internal PATH_INFO. SCRIPT_NAME makes Flask url_for prefix assets.
    """
    def __init__(self, app, base):
        self.app, self.base = app, base

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path == self.base or path.startswith(self.base + "/"):
            environ["PATH_INFO"] = path[len(self.base):] or "/"
        environ["SCRIPT_NAME"] = self.base
        return self.app(environ, start_response)


def configure_url_base(app, value):
    base = normalize_url_base(value)
    app.config["APPLICATION_ROOT"] = base or "/"
    app.config["SESSION_COOKIE_PATH"] = base or "/"
    if not base:
        return base
    app.wsgi_app = URLBaseMiddleware(app.wsgi_app, base)

    @app.after_request
    def prefix_redirect(response):
        location = response.headers.get("Location", "")
        if location.startswith("/") and not location.startswith("//"):
            if location != base and not location.startswith(base + "/"):
                response.headers["Location"] = base + location
        # API-produced artwork/export URLs are also consumed as CSS values and
        # direct navigation targets. Rewrite URL fields, never library file paths.
        if response.is_json and response.status_code not in (204, 304) and not response.direct_passthrough:
            def urls(value, key=""):
                if isinstance(value, dict):
                    return {k: urls(v, k) for k, v in value.items()}
                if isinstance(value, list):
                    return [urls(item, key) for item in value]
                is_url = key.lower().endswith("url") or key.lower() in {
                    "src", "image", "poster", "backdrop", "thumb", "cover", "art"}
                if is_url and isinstance(value, str) and value.startswith(("/api/", "/static/", "/stream/", "/auth/")):
                    return base + value
                return value
            response.set_data(app.json.dumps(urls(response.get_json())))
        # The initial HTML is parsed before client hooks can run. Prefix its URL
        # attributes here; subsequent generated markup uses the same client rule.
        if response.mimetype == "text/html" and not response.direct_passthrough:
            def replace(match):
                path = match.group(3)
                if path == base or path.startswith(base + "/"):
                    return match.group(0)
                return match.group(1) + match.group(2) + base + path
            text = re.sub(r"((?:href|src|action|poster)\s*=\s*)([\"'])(/(?!/)[^\"']*)",
                          replace, response.get_data(as_text=True), flags=re.I)
            response.set_data(text)
        return response
    return base

"""
Settings and API key management endpoints.
"""

from flask import request, current_app
from .auth import require_api_key, generate_api_key, _hash_key
from .helpers import api_success, api_error

# Keys that must NEVER be exposed via the API
_SENSITIVE_KEYS = {
    "spotify.client_secret",
    "soulseek.api_key",
    "plex.token",
    "jellyfin.api_key",
    "navidrome.password",
    "hydrabase.api_key",
    "tidal_download.session",
    "listenbrainz.token",
}


def register_routes(bp):

    # ---- Settings ----

    @bp.route("/settings", methods=["GET"])
    @require_api_key
    def get_settings():
        """Get current settings (sensitive values redacted)."""
        try:
            cfg = current_app.soulsync["config_manager"]
            raw = dict(cfg.config_data) if hasattr(cfg, "config_data") else {}

            sanitized = _redact_sensitive(raw)
            return api_success({"settings": sanitized})
        except Exception as e:
            return api_error("SETTINGS_ERROR", str(e), 500)

    @bp.route("/settings", methods=["PATCH"])
    @require_api_key
    def update_settings():
        """Update settings (partial).

        Body: {"key": "value", ...}  — dot-notation keys accepted.
        """
        body = request.get_json(silent=True) or {}
        if not body:
            return api_error("BAD_REQUEST", "Empty body.", 400)

        try:
            cfg = current_app.soulsync["config_manager"]
            updated = []
            for key, value in body.items():
                # Block writing API keys through settings endpoint
                if key == "api_keys":
                    continue
                cfg.set(key, value)
                updated.append(key)

            return api_success({"message": "Settings updated.", "updated_keys": updated})
        except Exception as e:
            return api_error("SETTINGS_ERROR", str(e), 500)

    # ---- API Key Management ----

    @bp.route("/api-keys", methods=["GET"])
    @require_api_key
    def list_api_keys():
        """List all API keys (prefix + label only, never the full key)."""
        try:
            cfg = current_app.soulsync["config_manager"]
            keys = cfg.get("api_keys", [])
            return api_success({
                "keys": [
                    {
                        "id": k.get("id"),
                        "label": k.get("label", ""),
                        "key_prefix": k.get("key_prefix", ""),
                        "created_at": k.get("created_at"),
                        "last_used_at": k.get("last_used_at"),
                    }
                    for k in keys
                ]
            })
        except Exception as e:
            return api_error("SETTINGS_ERROR", str(e), 500)

    @bp.route("/api-keys", methods=["POST"])
    @require_api_key
    def create_api_key():
        """Generate a new API key.

        Body: {"label": "My Bot"}
        The raw key is returned ONCE in the response.
        """
        body = request.get_json(silent=True) or {}
        label = body.get("label", "")

        try:
            cfg = current_app.soulsync["config_manager"]
            raw_key, record = generate_api_key(label)
            keys = cfg.get("api_keys", [])
            keys.append(record)
            cfg.set("api_keys", keys)

            return api_success({
                "key": raw_key,
                "id": record["id"],
                "label": record["label"],
                "key_prefix": record["key_prefix"],
                "created_at": record["created_at"],
            }, status=201)
        except Exception as e:
            return api_error("SETTINGS_ERROR", str(e), 500)

    @bp.route("/api-keys/<key_id>", methods=["DELETE"])
    @require_api_key
    def revoke_api_key(key_id):
        """Revoke (delete) an API key by its ID."""
        try:
            cfg = current_app.soulsync["config_manager"]
            keys = cfg.get("api_keys", [])
            original_len = len(keys)
            keys = [k for k in keys if k.get("id") != key_id]

            if len(keys) == original_len:
                return api_error("NOT_FOUND", "API key not found.", 404)

            cfg.set("api_keys", keys)
            return api_success({"message": "API key revoked."})
        except Exception as e:
            return api_error("SETTINGS_ERROR", str(e), 500)

    # ---- Bootstrap endpoint (no auth required) ----

    @bp.route("/api-keys/bootstrap", methods=["POST"])
    def bootstrap_api_key():
        """Generate the first API key when none exist (no auth required).

        This endpoint only works when zero API keys are configured.
        Body: {"label": "My First Key"}
        """
        try:
            cfg = current_app.soulsync["config_manager"]
            existing = cfg.get("api_keys", [])
            if existing:
                return api_error("FORBIDDEN",
                                 "API keys already exist. Use an authenticated request to create more.", 403)

            body = request.get_json(silent=True) or {}
            label = body.get("label", "Default")

            raw_key, record = generate_api_key(label)
            cfg.set("api_keys", [record])

            return api_success({
                "key": raw_key,
                "id": record["id"],
                "label": record["label"],
                "key_prefix": record["key_prefix"],
                "created_at": record["created_at"],
            }, status=201)
        except Exception as e:
            return api_error("SETTINGS_ERROR", str(e), 500)


def _redact_sensitive(config, prefix=""):
    """Recursively redact sensitive values from a config dict."""
    if not isinstance(config, dict):
        return config

    result = {}
    for key, value in config.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if any(full_key.startswith(s) for s in _SENSITIVE_KEYS):
            result[key] = "***REDACTED***"
        elif isinstance(value, dict):
            result[key] = _redact_sensitive(value, full_key)
        else:
            result[key] = value
    return result

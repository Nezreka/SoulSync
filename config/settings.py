import copy
import json
import os
import sqlite3
from typing import Dict, Any, Optional
from cryptography.fernet import Fernet, InvalidToken
from pathlib import Path

class ConfigManager:
    def __init__(self, config_path: str = "config/config.json"):
        # Determine strict absolute path to settings.py directory to help resolve config.json
        # This handles cases where CWD is different (e.g. running from /Users vs /Users/project)
        self.base_dir = Path(__file__).parent.parent.absolute()
        
        # Check for environment variable override first (Unified logic with web_server.py)
        env_config_path = os.environ.get('SOULSYNC_CONFIG_PATH')
        if env_config_path:
            config_path = env_config_path
        
        # Resolve config path
        if os.path.isabs(config_path):
            self.config_path = Path(config_path)
        else:
            # Try to resolve relative to CWD first (legacy behavior), then relative to project root
            cwd_path = Path(config_path)
            project_path = self.base_dir / config_path
            
            if cwd_path.exists():
                self.config_path = cwd_path.absolute()
            elif project_path.exists():
                self.config_path = project_path
            else:
                # Default to project path even if it doesn't exist yet (for creation/fallback)
                self.config_path = project_path

        print(f"ConfigManager initialized with path: {self.config_path}")
        
        self.config_data: Dict[str, Any] = {}
        self._fernet: Optional[Fernet] = None
        
        # Use DATABASE_PATH env var, fallback to database/music_library.db
        db_path_env = os.environ.get('DATABASE_PATH')
        if db_path_env:
             self.database_path = Path(db_path_env)
        else:
             self.database_path = self.base_dir / "database" / "music_library.db"
             
        print(f"Database path set to: {self.database_path}")
             
        self.load_config(str(self.config_path))

    def load_config(self, config_path: str = None):
        """
        Load configuration from database or file.
        Can be called to reload settings into the existing instance.
        """
        if config_path:
            self.config_path = Path(config_path)
        
        self._load_config()

    # Dot-notation paths to sensitive config values that must be encrypted at rest.
    # Paths pointing to dicts encrypt the entire dict as a JSON blob.
    _SENSITIVE_PATHS = frozenset({
        # Spotify
        'spotify.client_id',
        'spotify.client_secret',
        # Tidal
        'tidal.client_id',
        'tidal.client_secret',
        'tidal_tokens',              # full dict (access/refresh tokens)
        'tidal_download.session',    # full dict (access/refresh/expiry)
        # Qobuz
        'qobuz.session',             # full dict (app_id, app_secret, user_auth_token)
        # Media servers
        'plex.token',
        'jellyfin.api_key',
        'navidrome.password',
        # Download sources
        'soulseek.api_key',
        'deezer_download.arl',
        'lidarr_download.api_key',
        # Enrichment services
        'listenbrainz.token',
        'acoustid.api_key',
        'lastfm.api_key',
        'lastfm.api_secret',
        'lastfm.session_key',
        'genius.access_token',
        # Deezer OAuth
        'deezer.app_id',
        'deezer.app_secret',
        'deezer.access_token',
        # Other
        'hydrabase.api_key',
        'discogs.token',
    })

    def _get_fernet(self) -> Fernet:
        """Return a cached Fernet instance, creating the key file if needed."""
        if self._fernet is not None:
            return self._fernet
        key_file = self.database_path.parent / ".encryption_key"
        # Migrate key from old location (config/) to new location (database/)
        old_key_file = self.config_path.parent / ".encryption_key"
        if not key_file.exists() and old_key_file.exists():
            try:
                import shutil
                shutil.move(str(old_key_file), str(key_file))
                print(f"[MIGRATE] Moved encryption key to {key_file}")
            except Exception:
                key_file = old_key_file  # Fall back to old location
        if key_file.exists():
            with open(key_file, 'rb') as f:
                key = f.read()
        else:
            key = Fernet.generate_key()
            key_file.parent.mkdir(parents=True, exist_ok=True)
            with open(key_file, 'wb') as f:
                f.write(key)
            try:
                key_file.chmod(0o600)
            except OSError:
                pass  # Windows may not support Unix permissions
        self._fernet = Fernet(key)
        return self._fernet

    def _encrypt_value(self, value) -> str:
        """Encrypt a config value (string or dict/list) into a Fernet token string."""
        f = self._get_fernet()
        if isinstance(value, (dict, list)):
            plaintext = json.dumps(value)
        else:
            plaintext = str(value)
        return f.encrypt(plaintext.encode('utf-8')).decode('ascii')

    def _decrypt_value(self, value):
        """Decrypt a Fernet token string back to the original value.
        If value is not encrypted (migration), returns it unchanged."""
        if not isinstance(value, str):
            return value
        # Fernet tokens always start with 'gAAAAA'
        if not value.startswith('gAAAAA'):
            return value
        try:
            f = self._get_fernet()
            decrypted = f.decrypt(value.encode('ascii')).decode('utf-8')
            # Only parse JSON for dicts/lists (starts with { or [).
            # Plain strings (including numeric ones like API keys) stay as strings.
            if decrypted and decrypted[0] in ('{', '['):
                try:
                    return json.loads(decrypted)
                except (json.JSONDecodeError, ValueError):
                    pass
            return decrypted
        except InvalidToken:
            # Key mismatch — encrypted with a different key (key file deleted/replaced)
            print(f"[ERROR] Failed to decrypt a config value — encryption key may have changed. "
                  f"Re-enter credentials in Settings or restore the original .encryption_key file.")
            return value
        except Exception:
            return value

    def _encrypt_sensitive(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Return a deep copy of config_data with sensitive values encrypted."""
        encrypted = copy.deepcopy(config_data)
        for path in self._SENSITIVE_PATHS:
            keys = path.split('.')
            # Navigate to the parent
            parent = encrypted
            for k in keys[:-1]:
                if isinstance(parent, dict) and k in parent:
                    parent = parent[k]
                else:
                    parent = None
                    break
            if parent is None or not isinstance(parent, dict):
                continue
            leaf = keys[-1]
            if leaf not in parent:
                continue
            value = parent[leaf]
            # Skip empty values (no point encrypting empty strings/dicts)
            if not value and value != 0:
                continue
            # Skip already-encrypted values (idempotent)
            if isinstance(value, str) and value.startswith('gAAAAA'):
                continue
            parent[leaf] = self._encrypt_value(value)
        return encrypted

    def _decrypt_sensitive(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Decrypt sensitive values in-place and return the config dict."""
        for path in self._SENSITIVE_PATHS:
            keys = path.split('.')
            parent = config_data
            for k in keys[:-1]:
                if isinstance(parent, dict) and k in parent:
                    parent = parent[k]
                else:
                    parent = None
                    break
            if parent is None or not isinstance(parent, dict):
                continue
            leaf = keys[-1]
            if leaf not in parent:
                continue
            parent[leaf] = self._decrypt_value(parent[leaf])
        return config_data

    def _migrate_encrypt_if_needed(self):
        """Re-save config to encrypt any plaintext sensitive values still in the DB."""
        try:
            # Read raw DB content to check if any sensitive value is still plaintext
            conn = sqlite3.connect(str(self.database_path))
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM metadata WHERE key = 'app_config'")
            row = cursor.fetchone()
            conn.close()
            if not row or not row[0]:
                return
            raw = json.loads(row[0])
            needs_migration = False
            for path in self._SENSITIVE_PATHS:
                keys = path.split('.')
                parent = raw
                for k in keys[:-1]:
                    if isinstance(parent, dict) and k in parent:
                        parent = parent[k]
                    else:
                        parent = None
                        break
                if parent is None or not isinstance(parent, dict):
                    continue
                leaf = keys[-1]
                if leaf not in parent:
                    continue
                value = parent[leaf]
                if not value and value != 0:
                    continue
                # If the value is NOT a Fernet token, it's still plaintext
                if not (isinstance(value, str) and value.startswith('gAAAAA')):
                    needs_migration = True
                    break
            if needs_migration:
                print("[MIGRATE] Encrypting sensitive config values at rest...")
                self._save_to_database(self.config_data)
                print("[OK] Sensitive config values encrypted successfully")
        except Exception as e:
            print(f"[WARN] Could not migrate encryption: {e}")

    def _ensure_database_exists(self):
        """Ensure database file and metadata table exist"""
        try:
            # Create database directory if it doesn't exist
            self.database_path.parent.mkdir(parents=True, exist_ok=True)

            # Connect to database (creates file if it doesn't exist)
            conn = sqlite3.connect(str(self.database_path), timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.cursor()

            # Create metadata table if it doesn't exist
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Warning: Could not ensure database exists: {e}")

    def _load_from_database(self) -> Optional[Dict[str, Any]]:
        """Load configuration from database, decrypting sensitive values."""
        conn = None
        try:
            self._ensure_database_exists()

            conn = sqlite3.connect(str(self.database_path), timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM metadata WHERE key = 'app_config'")
            row = cursor.fetchone()

            if row and row[0]:
                config_data = json.loads(row[0])
                # Decrypt sensitive values (gracefully handles plaintext migration)
                config_data = self._decrypt_sensitive(config_data)
                print("[OK] Configuration loaded from database")
                return config_data
            else:
                return None

        except Exception as e:
            print(f"Warning: Could not load config from database: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def _save_to_database(self, config_data: Dict[str, Any]) -> bool:
        """Save configuration to database, encrypting sensitive values."""
        conn = None
        try:
            self._ensure_database_exists()

            # Encrypt sensitive values before writing (original dict is untouched)
            encrypted_data = self._encrypt_sensitive(config_data)

            # Use longer timeout (30s) to handle contention from enrichment workers
            conn = sqlite3.connect(str(self.database_path), timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.cursor()

            config_json = json.dumps(encrypted_data, indent=2)
            cursor.execute("""
                INSERT OR REPLACE INTO metadata (key, value, updated_at)
                VALUES ('app_config', ?, CURRENT_TIMESTAMP)
            """, (config_json,))

            conn.commit()
            return True

        except Exception as e:
            print(f"Error: Could not save config to database: {e}")
            return False
        finally:
            if conn:
                conn.close()

    def _load_from_config_file(self) -> Optional[Dict[str, Any]]:
        """Load configuration from config.json file (for migration)"""
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r') as f:
                    config_data = json.load(f)
                    print(f"[OK] Configuration loaded from {self.config_path}")
                    return config_data
            else:
                return None
        except Exception as e:
            print(f"Warning: Could not load config from file: {e}")
            return None

    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration"""
        return {
            "active_media_server": "plex",
            "spotify": {
                "client_id": "",
                "client_secret": "",
                "redirect_uri": "http://127.0.0.1:8888/callback"
            },
            "tidal": {
                "client_id": "",
                "client_secret": "",
                "redirect_uri": "http://127.0.0.1:8889/tidal/callback"
            },
            "plex": {
                "base_url": "",
                "token": "",
                "auto_detect": True
            },
            "jellyfin": {
                "base_url": "",
                "api_key": "",
                "auto_detect": True
            },
            "navidrome": {
                "base_url": "",
                "username": "",
                "password": "",
                "auto_detect": True
            },
            "soulseek": {
                "slskd_url": "",
                "api_key": "",
                "download_path": "./downloads",
                "transfer_path": "./Transfer",
                "max_peer_queue": 0,
                "download_timeout": 600
            },
            "download_source": {
                "mode": "soulseek",  # Options: "soulseek", "youtube", "tidal", "qobuz", "hifi", "hybrid"
                "hybrid_primary": "soulseek",  # Legacy: primary source for hybrid mode
                "hybrid_secondary": "youtube",  # Legacy: fallback source for hybrid mode
                "hybrid_order": [],  # Ordered list of sources for hybrid mode (overrides primary/secondary)
                "stream_source": "youtube",  # Options: "youtube" (instant, default), "active" (use download source; falls back to youtube if soulseek)
            },
            "tidal_download": {
                "quality": "lossless",  # Options: "low", "high", "lossless", "hires"
                "session": {
                    "token_type": "",
                    "access_token": "",
                    "refresh_token": "",
                    "expiry_time": 0
                }
            },
            "qobuz": {
                "quality": "lossless",  # Options: "mp3", "lossless", "hires", "hires_max"
                "session": {
                    "app_id": "",
                    "app_secret": "",
                    "user_auth_token": ""
                }
            },
            "hifi_download": {
                "quality": "lossless",  # Options: "low", "high", "lossless", "hires"
            },
            "lidarr_download": {
                "url": "",
                "api_key": "",
                "root_folder": "",
                "quality_profile": "Any",
                "cleanup_after_import": True,
            },
            "listenbrainz": {
                "base_url": "",
                "token": "",
                "scrobble_enabled": False
            },
            "acoustid": {
                "api_key": "",
                "enabled": False  # Disabled by default - requires API key and fpcalc
            },
            "lastfm": {
                "api_key": "",
                "api_secret": "",
                "session_key": "",
                "scrobble_enabled": False
            },
            "genius": {
                "access_token": ""
            },
            "logging": {
                "path": "logs/app.log",
                "level": "INFO"
            },
            "database": {
                "path": os.environ.get('DATABASE_PATH', 'database/music_library.db'),
                "max_workers": 5
            },
            "metadata_enhancement": {
                "enabled": True,
                "embed_album_art": True,
                "post_process_order": ["musicbrainz", "deezer", "audiodb", "tidal", "qobuz", "lastfm", "genius"]
            },
            "musicbrainz": {
                "embed_tags": True
            },
            "playlist_sync": {
                "create_backup": True
            },
            "settings": {
                "audio_quality": "flac"
            },
            "lossy_copy": {
                "enabled": False,
                "codec": "mp3",
                "bitrate": "320",
                "delete_original": False,
                "downsample_hires": False
            },
            "listening_stats": {
                "enabled": True,
                "poll_interval": 30
            },
            "library": {
                "music_paths": [],
                "music_videos_path": ""
            },
            "scripts": {
                "path": "./scripts",
                "timeout": 60
            },
            "import": {
                "staging_path": "./Staging",
                "replace_lower_quality": False
            },
            "m3u_export": {
                "enabled": False,
                "entry_base_path": ""
            },
            "youtube": {
                "cookies_browser": "",      # "", "chrome", "firefox", "edge", "brave", "opera", "safari"
                "download_delay": 3,        # seconds between sequential downloads
            },
            "hydrabase": {
                "url": "",
                "api_key": "",
                "auto_connect": False,
                "enabled": False
            },
            "content_filter": {
                "allow_explicit": True
            }
        }

    def _load_config(self):
        """
        Load configuration with priority:
        1. Database (primary storage)
        2. config.json (migration from file-based config)
        3. Defaults (fresh install)
        """
        print(f"Loading configuration...")
        
        # Try loading from database first
        config_data = self._load_from_database()

        if config_data:
            # Configuration exists in database
            self.config_data = config_data
            # Ensure sensitive values are encrypted at rest (one-time migration)
            self._migrate_encrypt_if_needed()
            return

        # Database is empty - try migration from config.json
        print(f"Configuration not found in database. Attempting migration from: {self.config_path}")
        config_data = self._load_from_config_file()

        if config_data:
            # Migrate from config.json to database
            print("[MIGRATE] Migrating configuration from config.json to database...")
            if self._save_to_database(config_data):
                print("[OK] Configuration migrated successfully to database.")
                self.config_data = config_data
                return
            else:
                print("[WARN] Migration failed - using file-based config temporarily.")
                self.config_data = config_data
                return

        # No config.json either - use defaults
        print("[INFO] ℹ️ No existing configuration found (DB or File) - using defaults")
        config_data = self._get_default_config()

        # Try to save defaults to database
        if self._save_to_database(config_data):
            print("[OK] Default configuration saved to database")
        else:
            print("[WARN] Could not save defaults to database - using in-memory config")

        self.config_data = config_data

    def _save_config(self):
        """Save configuration to database with retry on lock."""
        success = self._save_to_database(self.config_data)

        if not success:
            # Retry once after a brief wait (handles transient lock contention)
            import time
            time.sleep(1)
            success = self._save_to_database(self.config_data)

        if not success:
            # Fallback: Try to save to config.json if database fails
            print("[WARN] Database save failed - attempting file fallback")
            try:
                self.config_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.config_path, 'w') as f:
                    json.dump(self.config_data, f, indent=2)
                print("[OK] Configuration saved to config.json as fallback")
            except Exception as e:
                print(f"[ERROR] Failed to save configuration: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split('.')
        value = self.config_data

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value

    def set(self, key: str, value: Any):
        keys = key.split('.')
        config = self.config_data

        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]

        config[keys[-1]] = value
        self._save_config()

    def get_spotify_config(self) -> Dict[str, str]:
        return self.get('spotify', {})

    def get_plex_config(self) -> Dict[str, str]:
        return self.get('plex', {})

    def get_jellyfin_config(self) -> Dict[str, str]:
        return self.get('jellyfin', {})

    def get_navidrome_config(self) -> Dict[str, str]:
        return self.get('navidrome', {})

    def get_soulseek_config(self) -> Dict[str, str]:
        return self.get('soulseek', {})

    def get_hydrabase_config(self) -> Dict[str, str]:
        return self.get('hydrabase', {})

    def get_settings(self) -> Dict[str, Any]:
        return self.get('settings', {})

    def get_database_config(self) -> Dict[str, str]:
        return self.get('database', {})

    def get_logging_config(self) -> Dict[str, str]:
        return self.get('logging', {})

    def get_active_media_server(self) -> str:
        return self.get('active_media_server', 'plex')

    def set_active_media_server(self, server: str):
        """Set the active media server (plex, jellyfin, or navidrome)"""
        if server not in ['plex', 'jellyfin', 'navidrome']:
            raise ValueError(f"Invalid media server: {server}")
        self.set('active_media_server', server)

    def get_active_media_server_config(self) -> Dict[str, str]:
        """Get configuration for the currently active media server"""
        active_server = self.get_active_media_server()
        if active_server == 'plex':
            return self.get_plex_config()
        elif active_server == 'jellyfin':
            return self.get_jellyfin_config()
        elif active_server == 'navidrome':
            return self.get_navidrome_config()
        else:
            return {}

    def is_configured(self) -> bool:
        spotify = self.get_spotify_config()
        active_server = self.get_active_media_server()
        soulseek = self.get_soulseek_config()

        # Check active media server configuration
        media_server_configured = False
        if active_server == 'plex':
            plex = self.get_plex_config()
            media_server_configured = bool(plex.get('base_url')) and bool(plex.get('token'))
        elif active_server == 'jellyfin':
            jellyfin = self.get_jellyfin_config()
            media_server_configured = bool(jellyfin.get('base_url')) and bool(jellyfin.get('api_key'))
        elif active_server == 'navidrome':
            navidrome = self.get_navidrome_config()
            media_server_configured = bool(navidrome.get('base_url')) and bool(navidrome.get('username')) and bool(navidrome.get('password'))

        return (
            bool(spotify.get('client_id')) and
            bool(spotify.get('client_secret')) and
            media_server_configured and
            bool(soulseek.get('slskd_url'))
        )

    def validate_config(self) -> Dict[str, bool]:
        active_server = self.get_active_media_server()

        validation = {
            'spotify': bool(self.get('spotify.client_id')) and bool(self.get('spotify.client_secret')),
            'soulseek': bool(self.get('soulseek.slskd_url'))
        }

        # Validate all server types but mark active one
        validation['plex'] = bool(self.get('plex.base_url')) and bool(self.get('plex.token'))
        validation['jellyfin'] = bool(self.get('jellyfin.base_url')) and bool(self.get('jellyfin.api_key'))
        validation['navidrome'] = bool(self.get('navidrome.base_url')) and bool(self.get('navidrome.username')) and bool(self.get('navidrome.password'))
        validation['active_media_server'] = active_server

        return validation

config_manager = ConfigManager()

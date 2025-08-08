import json
import os
from typing import Dict, Any, Optional
from cryptography.fernet import Fernet
from pathlib import Path

class ConfigManager:
    def __init__(self, config_path: str = "config/config.json"):
        self.config_path = Path(config_path)
        self.config_data: Dict[str, Any] = {}
        self.encryption_key: Optional[bytes] = None
        self._load_config()
    
    def _get_encryption_key(self) -> bytes:
        key_file = self.config_path.parent / ".encryption_key"
        if key_file.exists():
            with open(key_file, 'rb') as f:
                return f.read()
        else:
            key = Fernet.generate_key()
            with open(key_file, 'wb') as f:
                f.write(key)
            key_file.chmod(0o600)
            return key
    
    def _load_config(self):
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
        
        with open(self.config_path, 'r') as f:
            self.config_data = json.load(f)
    
    def _save_config(self):
        with open(self.config_path, 'w') as f:
            json.dump(self.config_data, f, indent=2)
    
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
    
    def get_soulseek_config(self) -> Dict[str, str]:
        return self.get('soulseek', {})
    
    def get_settings(self) -> Dict[str, Any]:
        return self.get('settings', {})
    
    def get_database_config(self) -> Dict[str, str]:
        return self.get('database', {})
    
    def get_logging_config(self) -> Dict[str, str]:
        return self.get('logging', {})
    
    def is_configured(self) -> bool:
        spotify = self.get_spotify_config()
        plex = self.get_plex_config()
        soulseek = self.get_soulseek_config()
        
        return (
            bool(spotify.get('client_id')) and
            bool(spotify.get('client_secret')) and
            bool(plex.get('base_url')) and
            bool(plex.get('token')) and
            bool(soulseek.get('slskd_url'))
        )
    
    def validate_config(self) -> Dict[str, bool]:
        return {
            'spotify': bool(self.get('spotify.client_id')) and bool(self.get('spotify.client_secret')),
            'plex': bool(self.get('plex.base_url')) and bool(self.get('plex.token')),
            'soulseek': bool(self.get('soulseek.slskd_url'))
        }
    
    def get_quality_preference(self) -> str:
        """Get the user's preferred audio quality setting"""
        return self.get('settings.audio_quality', 'flac')

config_manager = ConfigManager()
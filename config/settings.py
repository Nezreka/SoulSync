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
    
    def get_jellyfin_config(self) -> Dict[str, str]:
        return self.get('jellyfin', {})
    
    def get_soulseek_config(self) -> Dict[str, str]:
        return self.get('soulseek', {})
    
    def get_settings(self) -> Dict[str, Any]:
        return self.get('settings', {})
    
    def get_database_config(self) -> Dict[str, str]:
        return self.get('database', {})
    
    def get_logging_config(self) -> Dict[str, str]:
        return self.get('logging', {})
    
    def get_active_media_server(self) -> str:
        return self.get('active_media_server', 'plex')
    
    def set_active_media_server(self, server: str):
        """Set the active media server (plex or jellyfin)"""
        if server not in ['plex', 'jellyfin']:
            raise ValueError(f"Invalid media server: {server}")
        self.set('active_media_server', server)
    
    def get_active_media_server_config(self) -> Dict[str, str]:
        """Get configuration for the currently active media server"""
        active_server = self.get_active_media_server()
        if active_server == 'plex':
            return self.get_plex_config()
        elif active_server == 'jellyfin':
            return self.get_jellyfin_config()
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
        
        # Validate both server types but mark active one
        validation['plex'] = bool(self.get('plex.base_url')) and bool(self.get('plex.token'))
        validation['jellyfin'] = bool(self.get('jellyfin.base_url')) and bool(self.get('jellyfin.api_key'))
        validation['active_media_server'] = active_server
        
        return validation
    
    def get_quality_preference(self) -> str:
        """Get the user's preferred audio quality setting"""
        return self.get('settings.audio_quality', 'flac')

config_manager = ConfigManager()
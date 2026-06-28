import unittest
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.metadata.registry import get_spotify_client_for_profile

# Mock config_manager as it's a global dependency
class MockConfigManager:
    def __init__(self):
        self.store = {}

    def get(self, key, default=None):
        return self.store.get(key, default)

    def get_spotify_config(self):
        return self.store.get('spotify', {})

    def set(self, key, value):
        self.store[key] = value

class TestSpotifyOAuthIntegration(unittest.TestCase):
    @patch('core.metadata.registry.get_spotify_client')
    @patch('core.metadata.registry._profile_spotify_credentials_provider')
    @patch('core.metadata.registry._get_config_value')
    @patch('spotipy.oauth2.SpotifyOAuth')
    @patch('core.spotify_client.normalize_spotify_oauth_config')
    def test_get_spotify_client_for_profile_uses_normalized_config(self, mock_normalize, mock_spotify_oauth, mock_get_config, mock_creds_provider, mock_get_global):
        # Set up mock config values
        mock_creds_provider.return_value = {
            "client_id": "  original_client_id  ",
            "client_secret": "  original_client_secret  ",
            "redirect_uri": "http://example.com/callback/"
        }
        
        # Make sure the file exists check passes
        with patch('os.path.exists', return_value=True):
            # Set up mock for normalize_spotify_oauth_config to return cleaned values
            mock_normalize.return_value = {
                "client_id": "cleaned_client_id",
                "client_secret": "cleaned_client_secret",
                "redirect_uri": "http://example.com/callback"
            }

            # Call the function under test with profile_id=2 (to bypass global client)
            get_spotify_client_for_profile(profile_id=2)

            # Assert that normalize_spotify_oauth_config was called with the original config
            mock_normalize.assert_any_call({
                "client_id": "  original_client_id  ",
                "client_secret": "  original_client_secret  ",
                "redirect_uri": "http://example.com/callback/"
            })

            # Assert that SpotifyOAuth was initialized with the normalized config
            mock_spotify_oauth.assert_called_once()
            args, kwargs = mock_spotify_oauth.call_args
            self.assertEqual(kwargs['client_id'], "cleaned_client_id")
            self.assertEqual(kwargs['client_secret'], "cleaned_client_secret")
            self.assertEqual(kwargs['redirect_uri'], "http://example.com/callback")
            self.assertEqual(kwargs['state'], 'profile_2')

    @patch('core.metadata.registry.get_spotify_client')
    @patch('core.metadata.registry._profile_spotify_credentials_provider')
    @patch('core.metadata.registry._get_config_value')
    @patch('spotipy.oauth2.SpotifyOAuth')
    @patch('core.spotify_client.normalize_spotify_oauth_config')
    def test_get_spotify_client_for_profile_handles_no_config(self, mock_normalize, mock_spotify_oauth, mock_get_config, mock_creds_provider, mock_get_global):
        # Simulate no spotify config
        mock_creds_provider.return_value = {}
        mock_get_config.side_effect = lambda key, default: "" if "client" in key else "http://127.0.0.1:8888/callback"
        
        # Ensure os.path.exists returns False so it doesn't try to use cache
        with patch('os.path.exists', return_value=False):
            # Call the function under test
            get_spotify_client_for_profile(profile_id=2)

            # It still reaches get_spotify_client() which is mocked.
            # In registry.py, the fallbacks for client_id/client_secret result in calls to get_spotify_client().
            mock_get_global.assert_called()


if __name__ == '__main__':
    unittest.main()

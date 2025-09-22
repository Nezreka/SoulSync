from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                           QFrame, QPushButton, QLineEdit, QComboBox,
                           QCheckBox, QSpinBox, QTextEdit, QGroupBox, QFormLayout, QMessageBox, QSizePolicy, QScrollArea)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from config.settings import config_manager
from utils.logging_config import get_logger

logger = get_logger("settings")

class PlexDetectionThread(QThread):
    progress_updated = pyqtSignal(int, str)  # progress value, current url
    detection_completed = pyqtSignal(str)  # found_url (empty if not found)
    
    def __init__(self):
        super().__init__()
        self.cancelled = False
    
    def cancel(self):
        self.cancelled = True
    
    def run(self):
        import requests
        import socket
        import ipaddress
        import subprocess
        import platform
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        def get_network_info():
            """Get comprehensive network information with subnet detection"""
            try:
                # Get local IP using socket method
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
                
                # Try to get actual subnet mask
                try:
                    if platform.system() == "Windows":
                        # Windows: Use netsh to get subnet info
                        result = subprocess.run(['netsh', 'interface', 'ip', 'show', 'config'], 
                                              capture_output=True, text=True, timeout=3)
                        # Parse output for subnet mask (simplified)
                        subnet_mask = "255.255.255.0"  # Default fallback
                    else:
                        # Linux/Mac: Try to parse network interfaces
                        result = subprocess.run(['ip', 'route', 'show'], 
                                              capture_output=True, text=True, timeout=3)
                        subnet_mask = "255.255.255.0"  # Default fallback
                except:
                    subnet_mask = "255.255.255.0"  # Default /24
                
                # Calculate network range
                network = ipaddress.IPv4Network(f"{local_ip}/{subnet_mask}", strict=False)
                return str(network.network_address), str(network.netmask), local_ip, network
                
            except Exception as e:
                # Fallback to original method
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
                
                # Default to /24 network
                network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
                return str(network.network_address), "255.255.255.0", local_ip, network
        
        def test_plex_server(ip, port=32400):
            """Test if a Plex server is running at the given IP and port"""
            try:
                url = f"http://{ip}:{port}/web/index.html"
                response = requests.get(url, timeout=2, allow_redirects=True)
                
                # Check for Plex-specific indicators
                if response.status_code == 200:
                    # Check if it's actually Plex
                    if 'plex' in response.text.lower() or 'X-Plex' in str(response.headers):
                        return f"http://{ip}:{port}"
                        
                    # Also try the API endpoint
                    api_url = f"http://{ip}:{port}/identity"
                    api_response = requests.get(api_url, timeout=1)
                    if api_response.status_code == 200 and 'MediaContainer' in api_response.text:
                        return f"http://{ip}:{port}"
                        
            except:
                pass
            return None
        
        try:
            network_addr, netmask, local_ip, network = get_network_info()
            
            # Build list of IPs to test
            test_ips = []
            
            # Priority 1: Test localhost first
            if not self.cancelled:
                self.progress_updated.emit(5, "http://localhost:32400")
                localhost_result = test_plex_server("localhost")
                if localhost_result:
                    self.detection_completed.emit(localhost_result)
                    return
            
            # Priority 2: Test local IP
            if not self.cancelled:
                self.progress_updated.emit(10, f"http://{local_ip}:32400")
                local_result = test_plex_server(local_ip)
                if local_result:
                    self.detection_completed.emit(local_result)
                    return
            
            # Priority 3: Test common IPs (router gateway, etc.)
            common_ips = [
                local_ip.rsplit('.', 1)[0] + '.1',  # Typical gateway
                local_ip.rsplit('.', 1)[0] + '.2',  # Alternative gateway
                local_ip.rsplit('.', 1)[0] + '.100', # Common static IP
            ]
            
            progress = 15
            for ip in common_ips:
                if self.cancelled:
                    break
                    
                self.progress_updated.emit(progress, f"http://{ip}:32400")
                result = test_plex_server(ip)
                if result:
                    self.detection_completed.emit(result)
                    return
                progress += 5
            
            # Priority 4: Scan the network range (limited to reasonable size)
            network_hosts = list(network.hosts())
            if len(network_hosts) > 50:
                # Limit scan to reasonable size for performance
                step = max(1, len(network_hosts) // 50)
                network_hosts = network_hosts[::step]
            
            progress_step = max(1, (85 - progress) // len(network_hosts))
            
            # Use ThreadPoolExecutor for concurrent scanning
            with ThreadPoolExecutor(max_workers=10) as executor:
                # Submit all tasks
                future_to_ip = {executor.submit(test_plex_server, str(ip)): str(ip) 
                               for ip in network_hosts}
                
                try:
                    for future in as_completed(future_to_ip):
                        if self.cancelled:
                            # Cancel all pending futures
                            for f in future_to_ip:
                                if not f.done():
                                    f.cancel()
                            break
                            
                        ip = future_to_ip[future]
                        progress = min(95, progress + progress_step)
                        self.progress_updated.emit(progress, f"http://{ip}:32400")
                        
                        try:
                            result = future.result()
                            if result:
                                # Cancel all pending futures before returning
                                for f in future_to_ip:
                                    if not f.done():
                                        f.cancel()
                                self.detection_completed.emit(result)
                                return
                        except:
                            pass
                finally:
                    # Ensure executor is properly shutdown
                    # Use wait=False if cancelled to avoid blocking
                    executor.shutdown(wait=not self.cancelled)
            
            # If we get here, no Plex server was found
            self.progress_updated.emit(100, "Scan complete")
            self.detection_completed.emit("")  # Empty string = not found
            
        except Exception as e:
            print(f"Plex detection error: {e}")
            self.detection_completed.emit("")  # Empty string = not found

class SlskdDetectionThread(QThread):
    progress_updated = pyqtSignal(int, str)  # progress value, current url
    detection_completed = pyqtSignal(str)  # found_url (empty if not found)
    
    def __init__(self):
        super().__init__()
        self.cancelled = False
    
    def cancel(self):
        self.cancelled = True
    
    def run(self):
        import requests
        import socket
        import ipaddress
        import subprocess
        import platform
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        def get_network_info():
            """Get comprehensive network information with subnet detection"""
            try:
                # Get local IP using socket method
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
                
                # Try to get actual subnet mask
                try:
                    if platform.system() == "Windows":
                        # Windows: Use netsh to get subnet info
                        result = subprocess.run(['netsh', 'interface', 'ip', 'show', 'config'], 
                                              capture_output=True, text=True, timeout=3)
                        # Parse output for subnet mask (simplified)
                        subnet_mask = "255.255.255.0"  # Default fallback
                    else:
                        # Linux/Mac: Try to parse network interfaces
                        result = subprocess.run(['ip', 'route', 'show'], 
                                              capture_output=True, text=True, timeout=3)
                        subnet_mask = "255.255.255.0"  # Default fallback
                except:
                    subnet_mask = "255.255.255.0"  # Default /24
                
                # Calculate network range
                network = ipaddress.IPv4Network(f"{local_ip}/{subnet_mask}", strict=False)
                return str(network.network_address), str(network.netmask), local_ip, network
                
            except Exception as e:
                # Fallback to original method
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.connect(("8.8.8.8", 80))
                    local_ip = s.getsockname()[0]
                    s.close()
                    
                    ip_parts = local_ip.split('.')
                    network_base = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}.0"
                    network = ipaddress.IPv4Network(f"{network_base}/24", strict=False)
                    return network_base, "255.255.255.0", local_ip, network
                except:
                    return None, None, None, None
        
        def get_active_ips_from_arp():
            """Get active IP addresses from ARP table"""
            active_ips = set()
            try:
                if platform.system() == "Windows":
                    result = subprocess.run(['arp', '-a'], capture_output=True, text=True, timeout=5)
                else:
                    result = subprocess.run(['arp', '-a'], capture_output=True, text=True, timeout=5)
                
                # Parse ARP output for IP addresses
                import re
                ip_pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'
                ips = re.findall(ip_pattern, result.stdout)
                active_ips.update(ips)
            except:
                pass
            return active_ips
        
        def generate_comprehensive_targets(network_info):
            """Generate comprehensive list of scan targets with priorities"""
            if not network_info[3]:  # network object
                return []
            
            network, local_ip = network_info[3], network_info[2]
            targets = []
            
            # Enhanced port list for slskd detection
            slskd_ports = [5030, 5031, 8080, 3000, 9000, 38477, 2416]
            
            # Priority 1: Infrastructure IPs (router, DNS, etc.)
            infrastructure_ips = [1, 2, 254, 253]
            for host_num in infrastructure_ips:
                try:
                    ip = str(network.network_address + host_num)
                    if ip != local_ip and ip in network:
                        for port in slskd_ports:
                            targets.append((f"http://{ip}:{port}", 1))  # Priority 1
                except:
                    continue
            
            # Priority 2: Get active IPs from ARP table
            active_ips = get_active_ips_from_arp()
            for ip in active_ips:
                try:
                    if ipaddress.IPv4Address(ip) in network and ip != local_ip:
                        for port in slskd_ports:
                            targets.append((f"http://{ip}:{port}", 2))  # Priority 2
                except:
                    continue
            
            # Priority 3: Common static IP ranges
            static_ranges = [
                range(100, 201),  # .100-.200 (common static)
                range(10, 100),   # .10-.99 (DHCP range)
                range(201, 254),  # .201-.253 (high static)
            ]
            
            for ip_range in static_ranges:
                for host_num in ip_range:
                    try:
                        ip = str(network.network_address + host_num)
                        if ip != local_ip and ip in network:
                            # Only add if not already in active IPs (avoid duplicates)
                            if ip not in active_ips:
                                for port in [5030, 5031, 8080]:  # Limit ports for full sweep
                                    targets.append((f"http://{ip}:{port}", 3))  # Priority 3
                    except:
                        continue
            
            # Sort by priority and return
            targets.sort(key=lambda x: x[1])
            return [target[0] for target in targets]
        
        def test_url_enhanced(url, timeout=2):
            """Enhanced URL testing with slskd-specific validation"""
            try:
                # Test main API endpoint
                response = requests.get(f"{url}/api/v0/session", timeout=timeout)
                if response.status_code in [200, 401]:
                    # Additional validation: check if it's really slskd
                    try:
                        app_response = requests.get(f"{url}/api/v0/application", timeout=1)
                        if app_response.status_code == 200:
                            data = app_response.json()
                            if 'name' in data and 'slskd' in data.get('name', '').lower():
                                return url, 'verified'
                    except:
                        pass
                    return url, 'probable'
            except requests.exceptions.ConnectionError:
                pass
            except requests.exceptions.Timeout:
                pass
            except Exception:
                pass
            return None, None
        
        def parallel_scan(targets, max_workers=15):
            """Scan targets in parallel with progressive timeout"""
            found_url = None
            completed_count = 0
            
            # Split into batches for better progress reporting
            batch_size = max(1, len(targets) // 10)  # 10 progress updates
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all tasks
                future_to_url = {
                    executor.submit(test_url_enhanced, target): target 
                    for target in targets
                }
                
                try:
                    # Process completed tasks
                    for future in as_completed(future_to_url):
                        if self.cancelled:
                            # Cancel remaining futures
                            for f in future_to_url:
                                if not f.done():
                                    f.cancel()
                            break
                        
                        completed_count += 1
                        progress = int((completed_count / len(targets)) * 100)
                        current_url = future_to_url[future]
                        
                        # Update progress
                        self.progress_updated.emit(progress, f"Scanning {current_url.split('//')[1]}")
                        
                        # Check result
                        try:
                            result_url, confidence = future.result()
                            if result_url:
                                found_url = result_url
                                self.progress_updated.emit(100, f"Found: {result_url}")
                                
                                # Cancel remaining futures for faster completion
                                for f in future_to_url:
                                    if not f.done():
                                        f.cancel()
                                break
                        except:
                            continue
                finally:
                    # Ensure executor is properly shutdown
                    # Use wait=False if cancelled to avoid blocking
                    executor.shutdown(wait=not self.cancelled)
            
            return found_url
        
        # Main detection logic
        found_url = None
        
        # Phase 1: Test local candidates first (fast)
        self.progress_updated.emit(5, "Checking local machine...")
        local_candidates = [
            "http://localhost:5030",
            "http://127.0.0.1:5030", 
            "http://localhost:5031", 
            "http://127.0.0.1:5031",
            "http://localhost:8080",
            "http://127.0.0.1:8080",
            "http://localhost:3000",
            "http://127.0.0.1:3000"
        ]
        
        for url in local_candidates:
            if self.cancelled:
                break
            result_url, confidence = test_url_enhanced(url, timeout=1)
            if result_url:
                found_url = result_url
                break
        
        # Phase 2: Network scanning if not found locally
        if not found_url and not self.cancelled:
            self.progress_updated.emit(10, "Analyzing network...")
            
            network_info = get_network_info()
            if network_info[0]:  # If we got network info
                targets = generate_comprehensive_targets(network_info)
                
                if targets:
                    self.progress_updated.emit(15, f"Scanning {len(targets)} network targets...")
                    found_url = parallel_scan(targets)
        
        # Emit completion
        if not self.cancelled:
            self.detection_completed.emit(found_url or "")

class ServiceTestThread(QThread):
    test_completed = pyqtSignal(str, bool, str)  # service, success, message
    
    def __init__(self, service_type, test_config):
        super().__init__()
        self.service_type = service_type
        self.test_config = test_config
    
    def run(self):
        """Run the service test in background thread"""
        try:
            if self.service_type == "spotify":
                success, message = self._test_spotify()
            elif self.service_type == "tidal":
                success, message = self._test_tidal()
            elif self.service_type == "plex":
                success, message = self._test_plex()
            elif self.service_type == "jellyfin":
                success, message = self._test_jellyfin()
            elif self.service_type == "navidrome":
                success, message = self._test_navidrome()
            elif self.service_type == "soulseek":
                success, message = self._test_soulseek()
            else:
                success, message = False, "Unknown service type"
                
            self.test_completed.emit(self.service_type, success, message)
            
        except Exception as e:
            self.test_completed.emit(self.service_type, False, f"Test failed: {str(e)}")
    
    def _test_spotify(self):
        """Test Spotify connection"""
        try:
            from core.spotify_client import SpotifyClient
            
            # Basic validation first
            if not self.test_config.get('client_id') or not self.test_config.get('client_secret'):
                return False, "✗ Please enter both Client ID and Client Secret"
            
            # Save temporarily to test
            original_client_id = config_manager.get('spotify.client_id')
            original_client_secret = config_manager.get('spotify.client_secret')
            
            config_manager.set('spotify.client_id', self.test_config['client_id'])
            config_manager.set('spotify.client_secret', self.test_config['client_secret'])
            
            # Test connection with timeout protection
            try:
                client = SpotifyClient()
                
                # Check if client was created successfully (has sp object)
                if client.sp is None:
                    message = "✗ Failed to create Spotify client.\nCheck your credentials."
                    success = False
                else:
                    # Try a simple auth check with timeout
                    try:
                        # This will trigger OAuth flow - user needs to complete it
                        if client.is_authenticated():
                            user_info = client.get_user_info()
                            username = user_info.get('display_name', 'Unknown') if user_info else 'Unknown'
                            message = f"✓ Spotify connection successful!\nConnected as: {username}"
                            success = True
                        else:
                            message = "✗ Spotify authentication failed.\nPlease complete the OAuth flow in your browser."
                            success = False
                    except Exception as auth_e:
                        message = f"✗ Spotify authentication failed:\n{str(auth_e)}"
                        success = False
                        
            except Exception as client_e:
                message = f"✗ Failed to create Spotify client:\n{str(client_e)}"
                success = False
            
            # Restore original values
            config_manager.set('spotify.client_id', original_client_id)
            config_manager.set('spotify.client_secret', original_client_secret)
            
            return success, message
            
        except Exception as e:
            # Restore original values even on exception
            try:
                config_manager.set('spotify.client_id', original_client_id)
                config_manager.set('spotify.client_secret', original_client_secret)
            except:
                pass
            return False, f"✗ Spotify test failed:\n{str(e)}"
    
    def _test_tidal(self):
        """Test Tidal connection"""
        try:
            from core.tidal_client import TidalClient
            
            # Basic validation first
            if not self.test_config.get('client_id') or not self.test_config.get('client_secret'):
                return False, "✗ Please enter both Client ID and Client Secret"
            
            # Save temporarily to test
            original_client_id = config_manager.get('tidal.client_id')
            original_client_secret = config_manager.get('tidal.client_secret')
            
            config_manager.set('tidal.client_id', self.test_config['client_id'])
            config_manager.set('tidal.client_secret', self.test_config['client_secret'])
            
            # Test connection with timeout protection
            try:
                client = TidalClient()
                
                # Test authentication - this will trigger OAuth flow if needed
                if client.is_authenticated() or client._ensure_valid_token():
                    user_info = client.get_user_info()
                    username = user_info.get('display_name', 'Tidal User') if user_info else 'Tidal User'
                    message = f"✓ Tidal connection successful!\nConnected as: {username}\nOAuth flow completed."
                    success = True
                else:
                    message = "✗ Tidal authentication failed.\nPlease complete the OAuth flow in your browser.\nCheck your credentials and redirect URI."
                    success = False
                    
            except Exception as client_e:
                message = f"✗ Failed to create Tidal client:\n{str(client_e)}"
                success = False
            
            # Restore original values
            config_manager.set('tidal.client_id', original_client_id)
            config_manager.set('tidal.client_secret', original_client_secret)
            
            return success, message
            
        except Exception as e:
            # Restore original values even on exception
            try:
                config_manager.set('tidal.client_id', original_client_id)
                config_manager.set('tidal.client_secret', original_client_secret)
            except:
                pass
            return False, f"✗ Tidal test failed:\n{str(e)}"
    
    def _test_plex(self):
        """Test Plex connection"""
        try:
            from core.plex_client import PlexClient
            
            # Save temporarily to test
            original_base_url = config_manager.get('plex.base_url')
            original_token = config_manager.get('plex.token')
            
            config_manager.set('plex.base_url', self.test_config['base_url'])
            config_manager.set('plex.token', self.test_config['token'])
            
            # Test connection
            client = PlexClient()
            if client.is_connected():
                server_name = client.server.friendlyName if client.server else 'Unknown'
                message = f"✓ Plex connection successful!\nServer: {server_name}"
                success = True
            else:
                message = "✗ Plex connection failed.\nCheck your server URL and token."
                success = False
            
            # Restore original values
            config_manager.set('plex.base_url', original_base_url)
            config_manager.set('plex.token', original_token)
            
            return success, message
            
        except Exception as e:
            return False, f"✗ Plex test failed:\n{str(e)}"
    
    def _test_jellyfin(self):
        """Test Jellyfin connection"""
        try:
            import requests
            
            base_url = self.test_config['base_url']
            api_key = self.test_config['api_key']
            
            if not base_url:
                return False, "Please enter Jellyfin server URL"
            
            if not api_key:
                return False, "Please enter Jellyfin API key"
            
            # Clean URL - remove trailing slash
            if base_url.endswith('/'):
                base_url = base_url[:-1]
            
            # Test connection with system info endpoint
            headers = {'X-Emby-Token': api_key} if api_key else {}
            test_url = f"{base_url}/System/Info"
            
            response = requests.get(test_url, headers=headers, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                server_name = data.get('ServerName', 'Unknown')
                version = data.get('Version', 'Unknown')
                message = f"✓ Jellyfin connection successful!\nServer: {server_name}\nVersion: {version}"
                return True, message
            elif response.status_code == 401:
                return False, "✗ Jellyfin authentication failed.\nCheck your API key."
            else:
                return False, f"✗ Jellyfin connection failed.\nHTTP {response.status_code}: {response.text}"
                
        except requests.exceptions.Timeout:
            return False, "✗ Jellyfin connection timeout.\nCheck your server URL."
        except requests.exceptions.ConnectionError:
            return False, "✗ Cannot connect to Jellyfin server.\nCheck your server URL and network."
        except Exception as e:
            return False, f"✗ Jellyfin test failed:\n{str(e)}"

    def _test_navidrome(self):
        """Test Navidrome connection"""
        try:
            import requests
            import hashlib
            import secrets

            base_url = self.test_config['base_url']
            username = self.test_config['username']
            password = self.test_config['password']

            if not base_url:
                return False, "Please enter Navidrome server URL"

            if not username:
                return False, "Please enter Navidrome username"

            if not password:
                return False, "Please enter Navidrome password"

            # Clean URL - remove trailing slash
            if base_url.endswith('/'):
                base_url = base_url[:-1]

            # Generate authentication parameters for Subsonic API
            salt = secrets.token_hex(8)
            token = hashlib.md5((password + salt).encode()).hexdigest()

            # Test connection with ping endpoint
            params = {
                'u': username,
                't': token,
                's': salt,
                'v': '1.16.1',
                'c': 'SoulSync',
                'f': 'json'
            }

            test_url = f"{base_url}/rest/ping"
            response = requests.get(test_url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                subsonic_response = data.get('subsonic-response', {})

                if subsonic_response.get('status') == 'ok':
                    version = subsonic_response.get('version', 'Unknown')
                    message = f"✓ Navidrome connection successful!\nSubsonic API Version: {version}"
                    return True, message
                elif subsonic_response.get('status') == 'failed':
                    error = subsonic_response.get('error', {})
                    error_message = error.get('message', 'Unknown error')
                    return False, f"✗ Navidrome authentication failed:\n{error_message}"
                else:
                    return False, "✗ Unexpected response from Navidrome server"
            else:
                return False, f"✗ Navidrome connection failed.\nHTTP {response.status_code}: {response.text}"

        except requests.exceptions.Timeout:
            return False, "✗ Navidrome connection timeout.\nCheck your server URL."
        except requests.exceptions.ConnectionError:
            return False, "✗ Cannot connect to Navidrome server.\nCheck your server URL and network."
        except Exception as e:
            return False, f"✗ Navidrome test failed:\n{str(e)}"

    def _test_soulseek(self):
        """Test Soulseek connection"""
        try:
            import requests
            
            slskd_url = self.test_config['slskd_url']
            api_key = self.test_config['api_key']
            
            if not slskd_url:
                return False, ("Please enter slskd URL\n\n"
                             "slskd is a headless Soulseek client that provides an HTTP API.\n"
                             "Download from: https://github.com/slskd/slskd")
            
            # Test API endpoint
            headers = {}
            if api_key:
                headers['X-API-Key'] = api_key
            
            response = requests.get(f"{slskd_url}/api/v0/session", headers=headers, timeout=5)
            
            if response.status_code == 200:
                return True, "✓ Soulseek connection successful!\nslskd is responding."
            elif response.status_code == 401:
                return False, ("✗ Invalid API key\n\n"
                             "Please check your slskd API key in the configuration.")
            else:
                return False, (f"✗ Soulseek connection failed\nHTTP {response.status_code}\n\n"
                             "slskd is running but returned an error.")
                
        except requests.exceptions.ConnectionError as e:
            if "refused" in str(e).lower():
                return False, ("✗ Cannot connect to slskd\n\n"
                             "slskd appears to not be running on the specified URL.\n\n"
                             "To fix this:\n"
                             "1. Install slskd from: https://github.com/slskd/slskd\n"
                             "2. Start slskd service\n"
                             "3. Ensure it's running on the correct port (default: 5030)")
            else:
                return False, f"✗ Network error:\n{str(e)}"
        except requests.exceptions.Timeout:
            return False, ("✗ Connection timed out\n\n"
                         "slskd is not responding. Check if it's running and accessible.")
        except requests.exceptions.RequestException as e:
            return False, f"✗ Request failed:\n{str(e)}"
        except Exception as e:
            return False, f"✗ Unexpected error:\n{str(e)}"

class JellyfinDetectionThread(QThread):
    progress_updated = pyqtSignal(int, str)  # progress value, current url
    detection_completed = pyqtSignal(str)  # found_url (empty if not found)
    
    def __init__(self):
        super().__init__()
        self.cancelled = False
    
    def cancel(self):
        self.cancelled = True
    
    def run(self):
        import requests
        import socket
        import ipaddress
        import subprocess
        import platform
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        def get_network_info():
            """Get comprehensive network information with subnet detection"""
            try:
                # Get local IP using socket method
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
                
                # Parse network info
                network = ipaddress.ip_network(f"{local_ip}/24", strict=False)
                return {
                    'local_ip': local_ip,
                    'network': network,
                    'subnet': str(network.network_address) + "/24"
                }
            except Exception as e:
                print(f"Error getting network info: {e}")
                return {'local_ip': '127.0.0.1', 'network': None, 'subnet': '127.0.0.1/32'}
        
        try:
            # Test common Jellyfin URLs first
            common_urls = [
                "http://localhost:8096",
                "http://127.0.0.1:8096", 
                "http://jellyfin:8096"
            ]
            
            network_info = get_network_info()
            local_ip = network_info['local_ip']
            
            # Add local IP variations
            if local_ip != '127.0.0.1':
                common_urls.extend([
                    f"http://{local_ip}:8096",
                    f"https://{local_ip}:8920"  # HTTPS port
                ])
            
            # Test common URLs first
            for i, url in enumerate(common_urls):
                if self.cancelled:
                    break
                    
                progress = int((i / len(common_urls)) * 50)  # First 50% for common URLs
                self.progress_updated.emit(progress, url)
                
                if self.test_jellyfin_url(url):
                    self.detection_completed.emit(url)
                    return
            
            # If common URLs fail, scan network subnet
            if network_info['network'] and not self.cancelled:
                network = network_info['network']
                hosts_to_scan = list(network.hosts())[:50]  # Limit to first 50 hosts
                
                def test_host(host_ip):
                    if self.cancelled:
                        return None
                    
                    test_urls = [
                        f"http://{host_ip}:8096",
                        f"https://{host_ip}:8920"
                    ]
                    
                    for url in test_urls:
                        if self.cancelled:
                            break
                        if self.test_jellyfin_url(url, timeout=2):  # Shorter timeout for network scan
                            return url
                    return None
                
                # Test hosts in parallel
                with ThreadPoolExecutor(max_workers=10) as executor:
                    future_to_host = {executor.submit(test_host, str(host)): host for host in hosts_to_scan}
                    
                    for i, future in enumerate(as_completed(future_to_host)):
                        if self.cancelled:
                            break
                            
                        progress = 50 + int((i / len(hosts_to_scan)) * 50)  # Remaining 50%
                        host = future_to_host[future]
                        self.progress_updated.emit(progress, f"Scanning {host}...")
                        
                        result = future.result()
                        if result:
                            self.detection_completed.emit(result)
                            return
            
            # Nothing found
            self.detection_completed.emit("")
            
        except Exception as e:
            print(f"Jellyfin detection error: {e}")
            self.detection_completed.emit("")  # Empty string = not found
    
    def test_jellyfin_url(self, url, timeout=5):
        """Test if a URL hosts a Jellyfin server"""
        try:
            import requests
            
            # Test the system/info endpoint which is available without auth
            response = requests.get(f"{url}/System/Info", timeout=timeout, verify=False)
            
            if response.status_code == 200:
                data = response.json()
                # Check if it's actually Jellyfin
                if 'ServerName' in data or 'Version' in data:
                    return True
                    
        except Exception:
            pass
        
        # Fallback: try to get the web interface
        try:
            import requests
            response = requests.get(url, timeout=timeout, verify=False)
            if response.status_code == 200:
                content = response.text.lower()
                # Look for Jellyfin-specific content
                if 'jellyfin' in content or 'emby' in content:
                    return True
        except Exception:
            pass
            
        return False

class SettingsGroup(QGroupBox):
    def __init__(self, title: str, parent=None):
        super().__init__(title, parent)
        self.setStyleSheet("""
            QGroupBox {
                background: #282828;
                border: 1px solid #404040;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
                color: #ffffff;
                padding-top: 15px;
                margin-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)

class SettingsPage(QWidget):
    settings_changed = pyqtSignal(str, str)  # Signal for when settings paths change
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.config_manager = None
        self.form_inputs = {}
        self.test_thread = None
        self.test_buttons = {}
        self.detection_thread = None
        self.detection_dialog = None
        self.setup_ui()
        self.load_config_values()
    
    def set_toast_manager(self, toast_manager):
        """Set the toast manager for showing notifications"""
        self.toast_manager = toast_manager
    
    def on_test_completed(self, service, success, message):
        """Handle test completion from background thread"""
        # Re-enable the test button
        if service in self.test_buttons:
            button = self.test_buttons[service]
            button.setEnabled(True)
            button.setText(f"Test {service.title()}")
        
        # Show result message
        if success:
            QMessageBox.information(self, "Success", message)
        else:
            if "Configuration Required" in message or "enter slskd URL" in message:
                QMessageBox.warning(self, "Configuration Required", message)
            else:
                QMessageBox.critical(self, "Test Failed", message)
        
        # Clean up thread
        if self.test_thread:
            self.test_thread.deleteLater()
            self.test_thread = None
    
    def start_service_test(self, service_type, test_config):
        """Start a service test in background thread"""
        # Don't start new test if one is already running
        if self.test_thread and self.test_thread.isRunning():
            return
        
        # Update button state
        if service_type in self.test_buttons:
            button = self.test_buttons[service_type]
            button.setEnabled(False)
            button.setText("Testing...")
        
        # Start test thread
        self.test_thread = ServiceTestThread(service_type, test_config)
        self.test_thread.test_completed.connect(self.on_test_completed)
        self.test_thread.start()
    
    def setup_ui(self):
        self.setStyleSheet("""
            SettingsPage {
                background: #191414;
            }
        """)
        
        # Main container layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Create scroll area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setStyleSheet("""
            QScrollArea {
                background: #191414;
                border: none;
            }
            QScrollBar:vertical {
                background: #282828;
                width: 12px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background: #535353;
                min-height: 20px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical:hover {
                background: #727272;
            }
        """)
        
        # Create scrollable content widget
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: #191414;")
        content_layout = QVBoxLayout(scroll_content)
        content_layout.setContentsMargins(20, 16, 20, 20)
        content_layout.setSpacing(16)
        
        # Header
        header = self.create_header()
        content_layout.addWidget(header)
        
        # Settings content
        settings_layout = QHBoxLayout()
        settings_layout.setSpacing(24)
        
        # Left column
        left_column = self.create_left_column()
        settings_layout.addWidget(left_column)
        
        # Right column
        right_column = self.create_right_column()
        settings_layout.addWidget(right_column)
        
        content_layout.addLayout(settings_layout)
        content_layout.addStretch()
        
        # Save button
        self.save_btn = QPushButton("💾 Save Settings")
        self.save_btn.setFixedHeight(45)
        self.save_btn.clicked.connect(self.save_settings)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: none;
                border-radius: 22px;
                color: #000000;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1ed760;
            }
        """)
        
        content_layout.addWidget(self.save_btn)
        
        # Set scroll area content and add to main layout
        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)
    
    def load_config_values(self):
        """Load current configuration values into form inputs"""
        try:
            # Load Spotify config
            spotify_config = config_manager.get_spotify_config()
            self.client_id_input.setText(spotify_config.get('client_id', ''))
            self.client_secret_input.setText(spotify_config.get('client_secret', ''))
            
            # Load Tidal config
            tidal_config = config_manager.get('tidal', {})
            self.tidal_client_id_input.setText(tidal_config.get('client_id', ''))
            self.tidal_client_secret_input.setText(tidal_config.get('client_secret', ''))
            
            # Load Plex config
            plex_config = config_manager.get_plex_config()
            self.plex_url_input.setText(plex_config.get('base_url', ''))
            self.plex_token_input.setText(plex_config.get('token', ''))
            
            # Load Jellyfin config
            jellyfin_config = config_manager.get_jellyfin_config()
            self.jellyfin_url_input.setText(jellyfin_config.get('base_url', ''))
            self.jellyfin_api_key_input.setText(jellyfin_config.get('api_key', ''))
            
            # Initialize server selection
            active_server = config_manager.get_active_media_server()
            self.pending_server_change = None
            self.update_server_toggle_styles(active_server)
            
            # Show/hide appropriate containers based on active server
            if active_server == 'plex':
                self.plex_container.show()
                self.jellyfin_container.hide()
            else:
                self.plex_container.hide()
                self.jellyfin_container.show()
            
            # Load Soulseek config
            soulseek_config = config_manager.get_soulseek_config()
            self.slskd_url_input.setText(soulseek_config.get('slskd_url', ''))
            self.api_key_input.setText(soulseek_config.get('api_key', ''))
            self.download_path_input.setText(soulseek_config.get('download_path', './downloads'))
            self.transfer_path_input.setText(soulseek_config.get('transfer_path', './Transfer'))
            
            # Load database config
            database_config = config_manager.get('database', {})
            if hasattr(self, 'max_workers_combo'):
                max_workers = database_config.get('max_workers', 5)
                # Find the index of the current value in the combo box
                index = self.max_workers_combo.findText(str(max_workers))
                if index >= 0:
                    self.max_workers_combo.setCurrentIndex(index)
            
            # Load logging config (read-only display)
            logging_config = config_manager.get_logging_config()
            if hasattr(self, 'log_level_display'):
                self.log_level_display.setText(logging_config.get('level', 'DEBUG'))
            
            if hasattr(self, 'log_path_display'):
                self.log_path_display.setText(logging_config.get('path', 'logs/app.log'))
            
            # Load quality preference
            if hasattr(self, 'quality_combo'):
                audio_quality = config_manager.get('settings.audio_quality', 'FLAC')
                # Map config values to combo box text
                quality_mapping = {
                    'flac': 'FLAC',
                    'mp3_320': '320 kbps MP3',
                    'mp3_256': '256 kbps MP3', 
                    'mp3_192': '192 kbps MP3',
                    'any': 'Any'
                }
                display_quality = quality_mapping.get(audio_quality.lower(), audio_quality)
                index = self.quality_combo.findText(display_quality)
                if index >= 0:
                    self.quality_combo.setCurrentIndex(index)
                
            # Load metadata enhancement settings
            metadata_config = config_manager.get('metadata_enhancement', {})
            if hasattr(self, 'metadata_enabled_checkbox'):
                self.metadata_enabled_checkbox.setChecked(metadata_config.get('enabled', True))
            if hasattr(self, 'embed_album_art_checkbox'):
                self.embed_album_art_checkbox.setChecked(metadata_config.get('embed_album_art', True))
            
            # Load playlist sync settings
            playlist_sync_config = config_manager.get('playlist_sync', {})
            if hasattr(self, 'create_backup_checkbox'):
                self.create_backup_checkbox.setChecked(playlist_sync_config.get('create_backup', True))
            
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load configuration: {e}")
    
    def save_settings(self):
        """Save current form values to configuration"""
        try:
            # Save Spotify settings
            config_manager.set('spotify.client_id', self.client_id_input.text())
            config_manager.set('spotify.client_secret', self.client_secret_input.text())
            
            # Save Tidal settings
            config_manager.set('tidal.client_id', self.tidal_client_id_input.text())
            config_manager.set('tidal.client_secret', self.tidal_client_secret_input.text())
            
            # Save Plex settings
            config_manager.set('plex.base_url', self.plex_url_input.text())
            config_manager.set('plex.token', self.plex_token_input.text())
            
            # Save Jellyfin settings
            config_manager.set('jellyfin.base_url', self.jellyfin_url_input.text())
            config_manager.set('jellyfin.api_key', self.jellyfin_api_key_input.text())
            
            # Save pending server change if any
            if hasattr(self, 'pending_server_change') and self.pending_server_change:
                config_manager.set_active_media_server(self.pending_server_change)
                logger.info(f"Server changed to {self.pending_server_change} - restart required")
            
            # Save Soulseek settings
            config_manager.set('soulseek.slskd_url', self.slskd_url_input.text())
            config_manager.set('soulseek.api_key', self.api_key_input.text())
            config_manager.set('soulseek.download_path', self.download_path_input.text())
            config_manager.set('soulseek.transfer_path', self.transfer_path_input.text())
            
            # Save Database settings
            if hasattr(self, 'max_workers_combo'):
                max_workers = int(self.max_workers_combo.currentText())
                config_manager.set('database.max_workers', max_workers)
            
            # Save Quality preference
            if hasattr(self, 'quality_combo'):
                quality_text = self.quality_combo.currentText()
                # Map combo box text to config values
                config_mapping = {
                    'FLAC': 'flac',
                    '320 kbps MP3': 'mp3_320',
                    '256 kbps MP3': 'mp3_256',
                    '192 kbps MP3': 'mp3_192',
                    'Any': 'any'
                }
                config_value = config_mapping.get(quality_text, 'flac')
                config_manager.set('settings.audio_quality', config_value)
            
            # Emit signals for path changes to update other pages immediately
            self.settings_changed.emit('soulseek.download_path', self.download_path_input.text())
            self.settings_changed.emit('soulseek.transfer_path', self.transfer_path_input.text())
            
            # Emit signals for service configuration changes to reinitialize clients
            self.settings_changed.emit('spotify.client_id', self.client_id_input.text())
            self.settings_changed.emit('spotify.client_secret', self.client_secret_input.text())
            self.settings_changed.emit('tidal.client_id', self.tidal_client_id_input.text())
            self.settings_changed.emit('tidal.client_secret', self.tidal_client_secret_input.text())
            self.settings_changed.emit('plex.base_url', self.plex_url_input.text())
            self.settings_changed.emit('plex.token', self.plex_token_input.text())
            self.settings_changed.emit('soulseek.slskd_url', self.slskd_url_input.text())
            self.settings_changed.emit('soulseek.api_key', self.api_key_input.text())
            
            # Show success message
            QMessageBox.information(self, "Success", "Settings saved successfully!")
            
            # Update button text temporarily
            original_text = self.save_btn.text()
            self.save_btn.setText("✓ Saved!")
            self.save_btn.setStyleSheet("""
                QPushButton {
                    background: #1aa34a;
                    border: none;
                    border-radius: 22px;
                    color: #ffffff;
                    font-size: 14px;
                    font-weight: bold;
                }
            """)
            
            # Reset button after 2 seconds
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(2000, lambda: self.reset_save_button(original_text))
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save settings: {e}")
    
    def reset_save_button(self, original_text):
        """Reset save button to original state"""
        self.save_btn.setText(original_text)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background: #1db954;
                border: none;
                border-radius: 22px;
                color: #000000;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1ed760;
            }
        """)
    
    def test_spotify_connection(self):
        """Test Spotify API connection in background thread"""
        test_config = {
            'client_id': self.client_id_input.text(),
            'client_secret': self.client_secret_input.text()
        }
        self.start_service_test('spotify', test_config)
    
    def test_tidal_connection(self):
        """Test Tidal API connection in background thread"""
        test_config = {
            'client_id': self.tidal_client_id_input.text(),
            'client_secret': self.tidal_client_secret_input.text()
        }
        self.start_service_test('tidal', test_config)
    
    def authenticate_tidal(self):
        """Manually trigger Tidal OAuth authentication"""
        try:
            from core.tidal_client import TidalClient
            
            # Make sure we have the current settings
            config_manager.set('tidal.client_id', self.tidal_client_id_input.text())
            config_manager.set('tidal.client_secret', self.tidal_client_secret_input.text())
            
            # Create client and authenticate
            client = TidalClient()
            
            self.tidal_auth_btn.setText("🔐 Authenticating...")
            self.tidal_auth_btn.setEnabled(False)
            
            if client.authenticate():
                QMessageBox.information(self, "Success", "✓ Tidal authentication successful!\nYou can now use Tidal playlists.")
                self.tidal_auth_btn.setText("✅ Authenticated")
            else:
                QMessageBox.warning(self, "Authentication Failed", "✗ Tidal authentication failed.\nPlease check your credentials and try again.")
                self.tidal_auth_btn.setText("🔐 Authenticate")
            
            self.tidal_auth_btn.setEnabled(True)
            
        except Exception as e:
            self.tidal_auth_btn.setText("🔐 Authenticate")
            self.tidal_auth_btn.setEnabled(True)
            QMessageBox.critical(self, "Error", f"Failed to authenticate with Tidal:\n{str(e)}")
    
    def test_active_server_connection(self):
        """Test the currently active (or pending) media server connection"""
        # Determine which server to test (pending change takes priority)
        active_server = getattr(self, 'pending_server_change', None) or config_manager.get_active_media_server()
        
        if active_server == 'plex':
            test_config = {
                'base_url': self.plex_url_input.text(),
                'token': self.plex_token_input.text()
            }
            self.start_service_test('plex', test_config)
        elif active_server == 'jellyfin':
            test_config = {
                'base_url': self.jellyfin_url_input.text(),
                'api_key': self.jellyfin_api_key_input.text()
            }
            self.start_service_test('jellyfin', test_config)
        elif active_server == 'navidrome':
            test_config = {
                'base_url': self.navidrome_url_input.text(),
                'username': self.navidrome_username_input.text(),
                'password': self.navidrome_password_input.text()
            }
            self.start_service_test('navidrome', test_config)
        else:
            logger.warning(f"Unknown active server type: {active_server}")
    
    def test_plex_connection(self):
        """Test Plex server connection in background thread"""
        test_config = {
            'base_url': self.plex_url_input.text(),
            'token': self.plex_token_input.text()
        }
        self.start_service_test('plex', test_config)
    
    def test_soulseek_connection(self):
        """Test Soulseek slskd connection in background thread"""
        test_config = {
            'slskd_url': self.slskd_url_input.text(),
            'api_key': self.api_key_input.text()
        }
        self.start_service_test('soulseek', test_config)
    
    def auto_detect_plex(self):
        """Auto-detect Plex server URL using background thread"""
        # Don't start new detection if one is already running
        if self.detection_thread and self.detection_thread.isRunning():
            return
        
        # Create animated loading dialog
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
        from PyQt6.QtCore import QTimer, QPropertyAnimation, QRect
        from PyQt6.QtGui import QPainter, QColor
        
        self.detection_dialog = QDialog(self)
        self.detection_dialog.setWindowTitle("Auto-detecting Plex Server")
        self.detection_dialog.setModal(True)
        self.detection_dialog.setFixedSize(400, 180)
        self.detection_dialog.setWindowFlags(self.detection_dialog.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        
        # Apply dark theme styling
        self.detection_dialog.setStyleSheet("""
            QDialog {
                background-color: #282828;
                color: #ffffff;
                border: 1px solid #404040;
                border-radius: 8px;
            }
            QLabel {
                color: #ffffff;
                font-size: 14px;
            }
            QPushButton {
                background-color: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                color: #ffffff;
                padding: 8px 16px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
        """)
        
        layout = QVBoxLayout(self.detection_dialog)
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Title label
        title_label = QLabel("Searching for Plex servers...")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        
        # Status label
        self.status_label = QLabel("Checking local machine...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #ffffff; font-size: 12px; background: transparent;")
        layout.addWidget(self.status_label)
        
        # Animated loading bar container
        loading_container = QLabel()
        loading_container.setFixedHeight(8)
        loading_container.setStyleSheet("""
            QLabel {
                background-color: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
            }
        """)
        layout.addWidget(loading_container)
        
        # Animated orange bar for Plex
        self.loading_bar = QLabel(loading_container)
        self.loading_bar.setFixedHeight(6)
        self.loading_bar.setStyleSheet("""
            background-color: #e5a00d;
            border-radius: 3px;
            border: none;
        """)
        
        # Start animation
        self.loading_animation = QPropertyAnimation(self.loading_bar, b"geometry")
        self.loading_animation.setDuration(1500)  # 1.5 seconds
        self.loading_animation.setStartValue(QRect(1, 1, 0, 6))
        self.loading_animation.setEndValue(QRect(1, 1, loading_container.width() - 2, 6))
        self.loading_animation.setLoopCount(-1)  # Infinite loop
        self.loading_animation.start()
        
        # Cancel button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.cancel_detection)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
        
        # Start Plex detection thread
        self.detection_thread = PlexDetectionThread()
        self.detection_thread.progress_updated.connect(self.on_detection_progress, Qt.ConnectionType.QueuedConnection)
        self.detection_thread.detection_completed.connect(self.on_plex_detection_completed, Qt.ConnectionType.QueuedConnection)
        self.detection_thread.start()
        
        self.detection_dialog.show()
    
    def auto_detect_slskd(self):
        """Auto-detect slskd URL using background thread"""
        # Don't start new detection if one is already running
        if self.detection_thread and self.detection_thread.isRunning():
            return
        
        # Create animated loading dialog
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
        from PyQt6.QtCore import QTimer, QPropertyAnimation, QRect
        from PyQt6.QtGui import QPainter, QColor
        
        self.detection_dialog = QDialog(self)
        self.detection_dialog.setWindowTitle("Auto-detecting slskd")
        self.detection_dialog.setModal(True)
        self.detection_dialog.setFixedSize(400, 180)
        self.detection_dialog.setWindowFlags(self.detection_dialog.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        
        # Apply dark theme styling
        self.detection_dialog.setStyleSheet("""
            QDialog {
                background-color: #282828;
                color: #ffffff;
                border: 1px solid #404040;
                border-radius: 8px;
            }
            QLabel {
                color: #ffffff;
                font-size: 14px;
            }
            QPushButton {
                background-color: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                color: #ffffff;
                padding: 8px 16px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
        """)
        
        layout = QVBoxLayout(self.detection_dialog)
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Title label
        title_label = QLabel("Searching for slskd instances...")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        
        # Status label
        self.status_label = QLabel("Checking local machine...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #ffffff; font-size: 12px; background: transparent;")
        layout.addWidget(self.status_label)
        
        # Animated loading bar container
        loading_container = QLabel()
        loading_container.setFixedHeight(8)
        loading_container.setStyleSheet("""
            QLabel {
                background-color: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
            }
        """)
        layout.addWidget(loading_container)
        
        # Animated green bar
        self.loading_bar = QLabel(loading_container)
        self.loading_bar.setFixedHeight(6)
        self.loading_bar.setStyleSheet("""
            background-color: #1db954;
            border-radius: 3px;
            border: none;
        """)
        
        # Start animation
        self.loading_animation = QPropertyAnimation(self.loading_bar, b"geometry")
        self.loading_animation.setDuration(1500)  # 1.5 seconds
        self.loading_animation.setStartValue(QRect(1, 1, 0, 6))
        self.loading_animation.setEndValue(QRect(1, 1, loading_container.width() - 2, 6))
        self.loading_animation.setLoopCount(-1)  # Infinite loop
        self.loading_animation.start()
        
        # Cancel button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.cancel_detection)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
        
        # Start detection thread
        self.detection_thread = SlskdDetectionThread()
        self.detection_thread.progress_updated.connect(self.on_detection_progress, Qt.ConnectionType.QueuedConnection)
        self.detection_thread.detection_completed.connect(self.on_detection_completed, Qt.ConnectionType.QueuedConnection)
        self.detection_thread.start()
        
        self.detection_dialog.show()
    
    def cancel_detection(self):
        """Cancel the ongoing detection"""
        if self.detection_thread:
            # Set cancellation flag first
            self.detection_thread.cancel()
            
            # If thread is still running, terminate it
            if self.detection_thread.isRunning():
                self.detection_thread.quit()
                # Don't wait too long during cancellation to avoid blocking UI
                if not self.detection_thread.wait(500):  # Wait only 500ms
                    # Force terminate if it doesn't respond
                    self.detection_thread.terminate()
                    self.detection_thread.wait()
            
            self.detection_thread.deleteLater()
            self.detection_thread = None
        
        # Close dialog
        if hasattr(self, 'detection_dialog') and self.detection_dialog:
            if hasattr(self, 'loading_animation'):
                self.loading_animation.stop()
            self.detection_dialog.close()
            self.detection_dialog = None
    
    def on_detection_progress(self, progress_value, current_url):
        """Handle progress updates from detection thread"""
        if hasattr(self, 'status_label') and self.status_label:
            if "localhost" in current_url or "127.0.0.1" in current_url:
                self.status_label.setText("Checking local machine...")
            else:
                self.status_label.setText("Checking network...")
    
    def on_plex_detection_completed(self, found_url):
        """Handle Plex detection completion"""
        # Stop animation and close dialog
        if hasattr(self, 'loading_animation'):
            self.loading_animation.stop()
        
        if hasattr(self, 'detection_dialog') and self.detection_dialog:
            self.detection_dialog.close()
            self.detection_dialog = None
        
        # Properly cleanup thread
        if self.detection_thread:
            if self.detection_thread.isRunning():
                self.detection_thread.quit()
                self.detection_thread.wait(1000)  # Wait up to 1 second for thread to finish
            self.detection_thread.deleteLater()
            self.detection_thread = None
        
        if found_url:
            self.plex_url_input.setText(found_url)
            self.show_plex_success_dialog(found_url)
        else:
            QMessageBox.warning(self, "Auto-detect Failed", 
                              "Could not find Plex server running on local machine or network.\n\n"
                              "Please ensure Plex Media Server is running and try:\n"
                              "• Check if Plex Media Server service is started\n"
                              "• Verify firewall allows access to Plex port (32400)\n"
                              "• Enter the URL manually if on a different network\n\n"
                              "Common URLs:\n"
                              "• http://localhost:32400 (local default)\n"
                              "• http://192.168.1.100:32400 (network example)")
    
    def on_detection_completed(self, found_url):
        """Handle slskd detection completion"""
        # Stop animation and close dialog
        if hasattr(self, 'loading_animation'):
            self.loading_animation.stop()
        
        if hasattr(self, 'detection_dialog') and self.detection_dialog:
            self.detection_dialog.close()
            self.detection_dialog = None
        
        # Properly cleanup thread
        if self.detection_thread:
            if self.detection_thread.isRunning():
                self.detection_thread.quit()
                self.detection_thread.wait(1000)  # Wait up to 1 second for thread to finish
            self.detection_thread.deleteLater()
            self.detection_thread = None
        
        if found_url:
            self.slskd_url_input.setText(found_url)
            self.show_success_dialog(found_url)
        else:
            QMessageBox.warning(self, "Auto-detect Failed", 
                              "Could not find slskd running on local machine or network.\n\n"
                              "Please ensure slskd is running and try:\n"
                              "• Check if slskd service is started\n"
                              "• Verify firewall allows access to slskd port\n"
                              "• Enter the URL manually if on a different network\n\n"
                              "Common URLs:\n"
                              "• http://localhost:5030 (local default)\n"
                              "• http://192.168.1.100:5030 (network example)")
    
    def show_plex_success_dialog(self, found_url):
        """Show custom Plex success dialog with copy functionality"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QClipboard
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Plex Auto-detect Success")
        dialog.setModal(True)
        dialog.setFixedSize(380, 160)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        
        # Apply dark theme styling
        dialog.setStyleSheet("""
            QDialog {
                background-color: #282828;
                color: #ffffff;
                border: 1px solid #404040;
                border-radius: 8px;
            }
            QLabel {
                color: #ffffff;
                font-size: 12px;
            }
            QTextEdit {
                background-color: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                color: #ffffff;
                font-size: 11px;
                font-family: 'Courier New', monospace;
                padding: 8px;
            }
            QPushButton {
                background-color: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                color: #ffffff;
                padding: 6px 12px;
                font-size: 11px;
                min-width: 50px;
                min-height: 28px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
            #copyButton {
                background-color: #e5a00d;
                border: 1px solid #e5a00d;
                color: #000000;
                font-weight: bold;
                min-height: 28px;
            }
            #copyButton:hover {
                background-color: #f5b00d;
            }
        """)
        
        layout = QVBoxLayout(dialog)
        layout.setSpacing(8)
        layout.setContentsMargins(15, 15, 15, 15)
        
        # Success message
        location_type = "locally" if "localhost" in found_url or "127.0.0.1" in found_url else "on network"
        success_label = QLabel(f"✓ Found Plex server running {location_type}!")
        success_label.setStyleSheet("color: #e5a00d; font-size: 13px; font-weight: bold;")
        success_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(success_label)
        
        # URL display with copy functionality
        url_label = QLabel("Detected URL:")
        layout.addWidget(url_label)
        
        url_container = QHBoxLayout()
        url_container.setSpacing(5)
        
        url_display = QTextEdit()
        url_display.setPlainText(found_url)
        url_display.setReadOnly(True)
        url_display.setFixedHeight(30)
        url_display.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        url_container.addWidget(url_display)
        
        copy_btn = QPushButton("Copy")
        copy_btn.setObjectName("copyButton")
        copy_btn.setFixedSize(55, 30)
        copy_btn.clicked.connect(lambda: self.copy_to_clipboard(found_url, copy_btn))
        url_container.addWidget(copy_btn)
        
        layout.addLayout(url_container)
        
        # Info text
        info_label = QLabel("URL automatically filled in settings above.")
        info_label.setStyleSheet("color: #ffffff; font-size: 9px; font-style: italic; background: transparent;")
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info_label)
        
        # OK button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        ok_btn = QPushButton("OK")
        ok_btn.setFixedSize(60, 28)
        ok_btn.clicked.connect(dialog.accept)
        ok_btn.setDefault(True)
        button_layout.addWidget(ok_btn)
        
        layout.addLayout(button_layout)
        
        dialog.exec()
    
    def show_jellyfin_success_dialog(self, found_url):
        """Show custom Jellyfin success dialog with copy functionality"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QClipboard
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Jellyfin Auto-detect Success")
        dialog.setModal(True)
        dialog.setFixedSize(380, 160)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        
        # Apply dark theme styling
        dialog.setStyleSheet("""
            QDialog {
                background-color: #282828;
                color: #ffffff;
                border: 1px solid #404040;
                border-radius: 8px;
            }
            QLabel {
                color: #ffffff;
                font-size: 12px;
            }
            QTextEdit {
                background-color: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                color: #ffffff;
                font-size: 11px;
                font-family: 'Courier New', monospace;
                padding: 8px;
            }
            QPushButton {
                background-color: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                color: #ffffff;
                padding: 6px 12px;
                font-size: 11px;
                min-width: 50px;
                min-height: 28px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
            #copyButton {
                background-color: #aa5cc3;
                border: 1px solid #aa5cc3;
                color: #ffffff;
                font-weight: bold;
                min-height: 28px;
            }
            #copyButton:hover {
                background-color: #ba6cd3;
            }
        """)
        
        layout = QVBoxLayout(dialog)
        layout.setSpacing(8)
        layout.setContentsMargins(15, 15, 15, 15)
        
        # Success message
        location_type = "locally" if "localhost" in found_url or "127.0.0.1" in found_url else "on network"
        success_label = QLabel(f"✓ Found Jellyfin server running {location_type}!")
        success_label.setStyleSheet("color: #aa5cc3; font-size: 13px; font-weight: bold;")
        success_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(success_label)
        
        # URL display with copy functionality
        url_label = QLabel("Detected URL:")
        layout.addWidget(url_label)
        
        url_container = QHBoxLayout()
        url_container.setSpacing(5)
        
        url_display = QTextEdit()
        url_display.setPlainText(found_url)
        url_display.setReadOnly(True)
        url_display.setFixedHeight(30)
        url_display.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        url_container.addWidget(url_display)
        
        copy_btn = QPushButton("Copy")
        copy_btn.setObjectName("copyButton")
        copy_btn.setFixedSize(55, 30)
        copy_btn.clicked.connect(lambda: self.copy_to_clipboard(found_url, copy_btn))
        url_container.addWidget(copy_btn)
        
        layout.addLayout(url_container)
        
        # Info text
        info_label = QLabel("URL automatically filled in settings above.")
        info_label.setStyleSheet("color: #ffffff; font-size: 9px; font-style: italic; background: transparent;")
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info_label)
        
        # OK button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        ok_btn = QPushButton("OK")
        ok_btn.setFixedSize(60, 28)
        ok_btn.clicked.connect(dialog.accept)
        ok_btn.setDefault(True)
        button_layout.addWidget(ok_btn)
        
        layout.addLayout(button_layout)
        
        dialog.exec()
    
    def show_success_dialog(self, found_url):
        """Show custom slskd success dialog with copy functionality"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QClipboard
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Auto-detect Success")
        dialog.setModal(True)
        dialog.setFixedSize(380, 160)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        
        # Apply dark theme styling
        dialog.setStyleSheet("""
            QDialog {
                background-color: #282828;
                color: #ffffff;
                border: 1px solid #404040;
                border-radius: 8px;
            }
            QLabel {
                color: #ffffff;
                font-size: 12px;
            }
            QTextEdit {
                background-color: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                color: #ffffff;
                font-size: 11px;
                font-family: 'Courier New', monospace;
                padding: 8px;
            }
            QPushButton {
                background-color: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                color: #ffffff;
                padding: 6px 12px;
                font-size: 11px;
                min-width: 50px;
                min-height: 28px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
            #copyButton {
                background-color: #1db954;
                border: 1px solid #1db954;
                color: #000000;
                font-weight: bold;
                min-height: 28px;
            }
            #copyButton:hover {
                background-color: #1ed760;
            }
        """)
        
        layout = QVBoxLayout(dialog)
        layout.setSpacing(8)
        layout.setContentsMargins(15, 15, 15, 15)
        
        # Success message
        location_type = "locally" if "localhost" in found_url or "127.0.0.1" in found_url else "on network"
        success_label = QLabel(f"✓ Found slskd running {location_type}!")
        success_label.setStyleSheet("color: #1db954; font-size: 13px; font-weight: bold;")
        success_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(success_label)
        
        # URL display with copy functionality
        url_label = QLabel("Detected URL:")
        layout.addWidget(url_label)
        
        url_container = QHBoxLayout()
        url_container.setSpacing(5)
        
        url_display = QTextEdit()
        url_display.setPlainText(found_url)
        url_display.setReadOnly(True)
        url_display.setFixedHeight(30)
        url_display.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        url_container.addWidget(url_display)
        
        copy_btn = QPushButton("Copy")
        copy_btn.setObjectName("copyButton")
        copy_btn.setFixedSize(55, 30)
        copy_btn.clicked.connect(lambda: self.copy_to_clipboard(found_url, copy_btn))
        url_container.addWidget(copy_btn)
        
        layout.addLayout(url_container)
        
        # Info text
        info_label = QLabel("URL automatically filled in settings above.")
        info_label.setStyleSheet("color: #ffffff; font-size: 9px; font-style: italic; background: transparent;")
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info_label)
        
        # OK button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        ok_btn = QPushButton("OK")
        ok_btn.setFixedSize(60, 28)
        ok_btn.clicked.connect(dialog.accept)
        ok_btn.setDefault(True)
        button_layout.addWidget(ok_btn)
        
        layout.addLayout(button_layout)
        
        dialog.exec()
    
    def copy_to_clipboard(self, text, button):
        """Copy text to clipboard and show feedback"""
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer
        
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        
        # Show feedback
        original_text = button.text()
        button.setText("Copied!")
        button.setEnabled(False)
        
        # Reset button after 1 second with safe reference check
        def safe_reset():
            try:
                if button and not button.isHidden():  # Check if button still exists and is valid
                    button.setText(original_text)
                    button.setEnabled(True)
            except RuntimeError:
                # Button was deleted, ignore silently
                pass
        
        QTimer.singleShot(1000, safe_reset)
    
    def browse_download_path(self):
        """Open a directory dialog to select download path"""
        from PyQt6.QtWidgets import QFileDialog
        
        current_path = self.download_path_input.text()
        selected_path = QFileDialog.getExistingDirectory(
            self, 
            "Select Download Directory", 
            current_path if current_path else ".",
            QFileDialog.Option.ShowDirsOnly
        )
        
        if selected_path:
            self.download_path_input.setText(selected_path)
    
    def browse_transfer_path(self):
        """Open a directory dialog to select transfer path"""
        from PyQt6.QtWidgets import QFileDialog
        
        current_path = self.transfer_path_input.text()
        selected_path = QFileDialog.getExistingDirectory(
            self, 
            "Select Transfer Directory", 
            current_path if current_path else ".",
            QFileDialog.Option.ShowDirsOnly
        )
        
        if selected_path:
            self.transfer_path_input.setText(selected_path)
    
    def create_header(self):
        header = QWidget()
        layout = QVBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        # Title
        title_label = QLabel("Settings")
        title_label.setFont(QFont("Arial", 28, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #ffffff; background: transparent;")
        
        # Subtitle
        subtitle_label = QLabel("Configure your music sync and download preferences")
        subtitle_label.setFont(QFont("Arial", 14))
        subtitle_label.setStyleSheet("color: #ffffff; background: transparent;")
        
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        
        return header
    
    def create_left_column(self):
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setSpacing(18)
        
        # API Configuration
        api_group = SettingsGroup("API Configuration")
        api_layout = QVBoxLayout(api_group)
        api_layout.setContentsMargins(16, 20, 16, 16)
        api_layout.setSpacing(12)
        
        # Spotify settings
        spotify_frame = QFrame()
        spotify_frame.setStyleSheet("""
            QFrame {
                background: #333333;
                border: 1px solid #444444;
                border-radius: 8px;
                padding: 8px;
            }
        """)
        spotify_layout = QVBoxLayout(spotify_frame)
        spotify_layout.setSpacing(8)
        
        spotify_title = QLabel("Spotify")
        spotify_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        spotify_title.setStyleSheet("color: #1db954;")
        spotify_layout.addWidget(spotify_title)
        
        # Client ID
        client_id_label = QLabel("Client ID:")
        client_id_label.setStyleSheet(self.get_label_style(11))
        spotify_layout.addWidget(client_id_label)
        
        self.client_id_input = QLineEdit()
        self.client_id_input.setStyleSheet(self.get_input_style())
        self.client_id_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form_inputs['spotify.client_id'] = self.client_id_input
        spotify_layout.addWidget(self.client_id_input)
        
        # Client Secret
        client_secret_label = QLabel("Client Secret:")
        client_secret_label.setStyleSheet(self.get_label_style(11))
        spotify_layout.addWidget(client_secret_label)
        
        self.client_secret_input = QLineEdit()
        self.client_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.client_secret_input.setStyleSheet(self.get_input_style())
        self.client_secret_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form_inputs['spotify.client_secret'] = self.client_secret_input
        spotify_layout.addWidget(self.client_secret_input)
        
        # Callback URL info
        callback_info_label = QLabel("Required Redirect URI:")
        callback_info_label.setStyleSheet("color: #ffffff; font-size: 11px; margin-top: 8px; background: transparent;")
        spotify_layout.addWidget(callback_info_label)
        
        callback_url_label = QLabel("http://127.0.0.1:8888/callback")
        callback_url_label.setStyleSheet("""
            color: #1db954; 
            font-size: 11px; 
            font-family: 'Courier New', monospace;
            background-color: rgba(29, 185, 84, 0.1);
            border: 1px solid rgba(29, 185, 84, 0.3);
            border-radius: 4px;
            padding: 6px 8px;
            margin-bottom: 8px;
        """)
        callback_url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        spotify_layout.addWidget(callback_url_label)
        
        # Helper text
        helper_text = QLabel("Add this URL to your Spotify app's 'Redirect URIs' in the Spotify Developer Dashboard")
        helper_text.setStyleSheet("color: #ffffff; font-size: 10px; font-style: italic; background: transparent;")
        helper_text.setWordWrap(True)
        spotify_layout.addWidget(helper_text)
        
        # Tidal settings
        tidal_frame = QFrame()
        tidal_frame.setStyleSheet("""
            QFrame {
                background: #333333;
                border: 1px solid #444444;
                border-radius: 8px;
                padding: 8px;
            }
        """)
        tidal_layout = QVBoxLayout(tidal_frame)
        tidal_layout.setSpacing(8)
        
        tidal_title = QLabel("Tidal")
        tidal_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        tidal_title.setStyleSheet("color: #ff6600;")
        tidal_layout.addWidget(tidal_title)
        
        # Client ID
        tidal_client_id_label = QLabel("Client ID:")
        tidal_client_id_label.setStyleSheet(self.get_label_style(11))
        tidal_layout.addWidget(tidal_client_id_label)
        
        self.tidal_client_id_input = QLineEdit()
        self.tidal_client_id_input.setStyleSheet(self.get_input_style())
        self.tidal_client_id_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form_inputs['tidal.client_id'] = self.tidal_client_id_input
        tidal_layout.addWidget(self.tidal_client_id_input)
        
        # Client Secret
        tidal_client_secret_label = QLabel("Client Secret:")
        tidal_client_secret_label.setStyleSheet(self.get_label_style(11))
        tidal_layout.addWidget(tidal_client_secret_label)
        
        self.tidal_client_secret_input = QLineEdit()
        self.tidal_client_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.tidal_client_secret_input.setStyleSheet(self.get_input_style())
        self.tidal_client_secret_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form_inputs['tidal.client_secret'] = self.tidal_client_secret_input
        tidal_layout.addWidget(self.tidal_client_secret_input)
        
        # Helper text for Tidal
        tidal_helper_text = QLabel("Configure Tidal API credentials for playlist sync functionality")
        tidal_helper_text.setStyleSheet("color: #ffffff; font-size: 10px; font-style: italic; background: transparent;")
        tidal_helper_text.setWordWrap(True)
        tidal_layout.addWidget(tidal_helper_text)
        
        # OAuth info
        oauth_info_label = QLabel("Required Redirect URI:")
        oauth_info_label.setStyleSheet("color: #ffffff; font-size: 11px; margin-top: 8px; background: transparent;")
        tidal_layout.addWidget(oauth_info_label)
        
        oauth_url_label = QLabel("http://127.0.0.1:8889/tidal/callback")
        oauth_url_label.setStyleSheet("""
            color: #ff6600; 
            font-size: 11px; 
            font-family: 'Courier New', monospace;
            background: #2a2a2a;
            border: 1px solid #444444;
            border-radius: 4px;
            padding: 6px 8px;
            margin-bottom: 8px;
        """)
        oauth_url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tidal_layout.addWidget(oauth_url_label)
        
        # Authenticate button
        self.tidal_auth_btn = QPushButton("🔐 Authenticate")
        self.tidal_auth_btn.setFixedHeight(30)
        self.tidal_auth_btn.setStyleSheet("""
            QPushButton {
                background: #ff6600;
                border: none;
                border-radius: 15px;
                color: #ffffff;
                font-size: 11px;
                font-weight: bold;
                margin-top: 8px;
            }
            QPushButton:hover {
                background: #ff7700;
            }
            QPushButton:pressed {
                background: #e55500;
            }
        """)
        self.tidal_auth_btn.clicked.connect(self.authenticate_tidal)
        tidal_layout.addWidget(self.tidal_auth_btn)
        
        # Server Selection Toggle Buttons
        server_selection_container = QWidget()
        server_selection_container.setStyleSheet("background: transparent;")
        server_selection_layout = QVBoxLayout(server_selection_container)
        server_selection_layout.setContentsMargins(0, 12, 0, 12)
        server_selection_layout.setSpacing(8)
        
        # Server selection title
        server_title = QLabel("Media Server Source")
        server_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        server_title.setStyleSheet("color: #ffffff; background: transparent;")
        server_selection_layout.addWidget(server_title)
        
        # Toggle buttons container
        toggle_container = QHBoxLayout()
        toggle_container.setSpacing(8)
        
        # Plex toggle button
        self.plex_toggle_button = QPushButton()
        self.plex_toggle_button.setFixedHeight(40)
        self.plex_toggle_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.plex_toggle_button.clicked.connect(lambda: self.select_media_server('plex'))
        
        # Jellyfin toggle button
        self.jellyfin_toggle_button = QPushButton()
        self.jellyfin_toggle_button.setFixedHeight(40)
        self.jellyfin_toggle_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.jellyfin_toggle_button.clicked.connect(lambda: self.select_media_server('jellyfin'))
        
        toggle_container.addWidget(self.plex_toggle_button)
        toggle_container.addWidget(self.jellyfin_toggle_button)
        server_selection_layout.addLayout(toggle_container)
        
        # Restart warning (initially hidden)
        self.restart_warning_frame = QLabel("⚠️ Server change requires restart - Save settings then restart SoulSync")
        self.restart_warning_frame.setStyleSheet("""
            color: #ffc107; 
            font-size: 11px; 
            font-weight: bold; 
            background: transparent;
            margin: 8px 0px 4px 0px;
        """)
        self.restart_warning_frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.restart_warning_frame.hide()
        server_selection_layout.addWidget(self.restart_warning_frame)
        
        # Media Server Settings Container
        self.plex_container = QWidget()
        self.plex_container.setStyleSheet("background: transparent;")
        plex_container_layout = QVBoxLayout(self.plex_container)
        plex_container_layout.setContentsMargins(0, 0, 0, 0)
        plex_container_layout.setSpacing(0)
        
        # Plex settings
        plex_frame = QFrame()
        plex_frame.setStyleSheet("""
            QFrame {
                background: #333333;
                border: 1px solid #444444;
                border-radius: 8px;
                padding: 8px;
            }
        """)
        plex_layout = QVBoxLayout(plex_frame)
        plex_layout.setSpacing(8)
        
        plex_title = QLabel("Plex")
        plex_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        plex_title.setStyleSheet("color: #e5a00d;")
        plex_layout.addWidget(plex_title)
        
        # Server URL
        plex_url_label = QLabel("Server URL:")
        plex_url_label.setStyleSheet(self.get_label_style(11))
        plex_layout.addWidget(plex_url_label)
        
        plex_url_input_layout = QHBoxLayout()
        self.plex_url_input = QLineEdit()
        self.plex_url_input.setStyleSheet(self.get_input_style())
        self.plex_url_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form_inputs['plex.base_url'] = self.plex_url_input
        
        plex_detect_btn = QPushButton("Auto-detect")
        plex_detect_btn.setFixedSize(80, 30)
        plex_detect_btn.clicked.connect(self.auto_detect_plex)
        plex_detect_btn.setStyleSheet(self.get_test_button_style())
        
        plex_url_input_layout.addWidget(self.plex_url_input)
        plex_url_input_layout.addWidget(plex_detect_btn)
        plex_layout.addLayout(plex_url_input_layout)
        
        # Token
        plex_token_label = QLabel("Token:")
        plex_token_label.setStyleSheet(self.get_label_style(11))
        plex_layout.addWidget(plex_token_label)
        
        self.plex_token_input = QLineEdit()
        self.plex_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.plex_token_input.setStyleSheet(self.get_input_style())
        self.plex_token_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form_inputs['plex.token'] = self.plex_token_input
        plex_layout.addWidget(self.plex_token_input)
        
        # Add Plex frame to its container
        plex_container_layout.addWidget(plex_frame)
        
        # Jellyfin Settings Container
        self.jellyfin_container = QWidget()
        self.jellyfin_container.setStyleSheet("background: transparent;")
        jellyfin_container_layout = QVBoxLayout(self.jellyfin_container)
        jellyfin_container_layout.setContentsMargins(0, 0, 0, 0)
        jellyfin_container_layout.setSpacing(0)
        
        # Jellyfin settings
        jellyfin_frame = QFrame()
        jellyfin_frame.setStyleSheet("""
            QFrame {
                background: #333333;
                border: 1px solid #444444;
                border-radius: 8px;
                padding: 8px;
            }
        """)
        jellyfin_layout = QVBoxLayout(jellyfin_frame)
        jellyfin_layout.setSpacing(8)
        
        jellyfin_title = QLabel("Jellyfin")
        jellyfin_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        jellyfin_title.setStyleSheet("color: #aa5cc3;")  # Jellyfin purple color
        jellyfin_layout.addWidget(jellyfin_title)
        
        # Server URL
        jellyfin_url_label = QLabel("Server URL:")
        jellyfin_url_label.setStyleSheet(self.get_label_style(11))
        jellyfin_layout.addWidget(jellyfin_url_label)
        
        jellyfin_url_input_layout = QHBoxLayout()
        self.jellyfin_url_input = QLineEdit()
        self.jellyfin_url_input.setStyleSheet(self.get_input_style())
        self.jellyfin_url_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form_inputs['jellyfin.base_url'] = self.jellyfin_url_input
        
        jellyfin_detect_btn = QPushButton("Auto-detect")
        jellyfin_detect_btn.setFixedSize(80, 30)
        jellyfin_detect_btn.clicked.connect(self.auto_detect_jellyfin)
        jellyfin_detect_btn.setStyleSheet(self.get_test_button_style())
        
        jellyfin_url_input_layout.addWidget(self.jellyfin_url_input)
        jellyfin_url_input_layout.addWidget(jellyfin_detect_btn)
        jellyfin_layout.addLayout(jellyfin_url_input_layout)
        
        # API Key
        jellyfin_api_key_label = QLabel("API Key:")
        jellyfin_api_key_label.setStyleSheet(self.get_label_style(11))
        jellyfin_layout.addWidget(jellyfin_api_key_label)
        
        self.jellyfin_api_key_input = QLineEdit()
        self.jellyfin_api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.jellyfin_api_key_input.setStyleSheet(self.get_input_style())
        self.jellyfin_api_key_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form_inputs['jellyfin.api_key'] = self.jellyfin_api_key_input
        jellyfin_layout.addWidget(self.jellyfin_api_key_input)
        
        # Add Jellyfin frame to its container
        jellyfin_container_layout.addWidget(jellyfin_frame)
        
        # Soulseek settings
        soulseek_frame = QFrame()
        soulseek_frame.setStyleSheet("""
            QFrame {
                background: #333333;
                border: 1px solid #444444;
                border-radius: 8px;
                padding: 8px;
            }
        """)
        soulseek_layout = QVBoxLayout(soulseek_frame)
        soulseek_layout.setSpacing(8)
        
        soulseek_title = QLabel("Soulseek")
        soulseek_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        soulseek_title.setStyleSheet("color: #5dade2;")
        soulseek_layout.addWidget(soulseek_title)
        
        # slskd URL
        slskd_url_label = QLabel("slskd URL:")
        slskd_url_label.setStyleSheet(self.get_label_style(11))
        soulseek_layout.addWidget(slskd_url_label)
        
        url_input_layout = QHBoxLayout()
        self.slskd_url_input = QLineEdit()
        self.slskd_url_input.setStyleSheet(self.get_input_style())
        self.slskd_url_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form_inputs['soulseek.slskd_url'] = self.slskd_url_input
        
        detect_btn = QPushButton("Auto-detect")
        detect_btn.setFixedSize(80, 30)
        detect_btn.clicked.connect(self.auto_detect_slskd)
        detect_btn.setStyleSheet(self.get_test_button_style())
        
        url_input_layout.addWidget(self.slskd_url_input)
        url_input_layout.addWidget(detect_btn)
        soulseek_layout.addLayout(url_input_layout)
        
        # API Key
        api_key_label = QLabel("API Key:")
        api_key_label.setStyleSheet(self.get_label_style(11))
        soulseek_layout.addWidget(api_key_label)
        
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("Enter your slskd API key")
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setStyleSheet(self.get_input_style())
        self.api_key_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form_inputs['soulseek.api_key'] = self.api_key_input
        soulseek_layout.addWidget(self.api_key_input)
        
        api_layout.addWidget(spotify_frame)
        api_layout.addWidget(tidal_frame)
        api_layout.addWidget(server_selection_container)
        api_layout.addWidget(self.plex_container)
        api_layout.addWidget(self.jellyfin_container)
        api_layout.addWidget(soulseek_frame)
        
        # Test connections
        test_layout = QHBoxLayout()
        test_layout.setSpacing(12)
        
        self.test_buttons['spotify'] = QPushButton("Test Spotify")
        self.test_buttons['spotify'].setFixedHeight(30)
        self.test_buttons['spotify'].setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.test_buttons['spotify'].clicked.connect(self.test_spotify_connection)
        self.test_buttons['spotify'].setStyleSheet(self.get_test_button_style())
        
        self.test_buttons['tidal'] = QPushButton("Test Tidal")
        self.test_buttons['tidal'].setFixedHeight(30)
        self.test_buttons['tidal'].setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.test_buttons['tidal'].clicked.connect(self.test_tidal_connection)
        self.test_buttons['tidal'].setStyleSheet(self.get_test_button_style())
        
        self.test_buttons['server'] = QPushButton("Test Server")
        self.test_buttons['server'].setFixedHeight(30)
        self.test_buttons['server'].setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.test_buttons['server'].clicked.connect(self.test_active_server_connection)
        self.test_buttons['server'].setStyleSheet(self.get_test_button_style())
        
        self.test_buttons['soulseek'] = QPushButton("Test Soulseek")
        self.test_buttons['soulseek'].setFixedHeight(30)
        self.test_buttons['soulseek'].setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.test_buttons['soulseek'].clicked.connect(self.test_soulseek_connection)
        self.test_buttons['soulseek'].setStyleSheet(self.get_test_button_style())
        
        test_layout.addWidget(self.test_buttons['spotify'])
        test_layout.addWidget(self.test_buttons['tidal'])
        test_layout.addWidget(self.test_buttons['server'])
        test_layout.addWidget(self.test_buttons['soulseek'])
        
        api_layout.addLayout(test_layout)
        
        
        layout.addWidget(api_group)
        layout.addStretch()
        
        return column
    
    def create_right_column(self):
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setSpacing(18)
        
        # Download Settings
        download_group = SettingsGroup("Download Settings")
        download_layout = QVBoxLayout(download_group)
        download_layout.setContentsMargins(16, 20, 16, 16)
        download_layout.setSpacing(12)
        
        # Quality preference
        quality_layout = QHBoxLayout()
        quality_label = QLabel("Preferred Quality:")
        quality_label.setStyleSheet(self.get_label_style(12))
        
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["FLAC", "320 kbps MP3", "256 kbps MP3", "192 kbps MP3", "Any"])
        self.quality_combo.setCurrentText("FLAC")
        self.quality_combo.setStyleSheet(self.get_combo_style())
        self.quality_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.form_inputs['settings.audio_quality'] = self.quality_combo
        
        quality_layout.addWidget(quality_label)
        quality_layout.addWidget(self.quality_combo)
        
        # Download path
        path_container = QVBoxLayout()
        path_label = QLabel("Slskd Download Dir:")
        path_label.setStyleSheet(self.get_label_style(12))
        path_container.addWidget(path_label)
        
        path_input_layout = QHBoxLayout()
        self.download_path_input = QLineEdit("./downloads")
        self.download_path_input.setStyleSheet(self.get_input_style())
        self.download_path_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        browse_btn = QPushButton("Browse")
        browse_btn.setFixedSize(70, 30)
        browse_btn.clicked.connect(self.browse_download_path)
        browse_btn.setStyleSheet(self.get_test_button_style())
        
        path_input_layout.addWidget(self.download_path_input)
        path_input_layout.addWidget(browse_btn)
        path_container.addLayout(path_input_layout)

        # Transfer folder path
        transfer_path_container = QVBoxLayout()
        transfer_path_label = QLabel("Matched Transfer Dir (Plex Music Dir?):")
        transfer_path_label.setStyleSheet(self.get_label_style(12))
        transfer_path_container.addWidget(transfer_path_label)
        
        transfer_input_layout = QHBoxLayout()
        self.transfer_path_input = QLineEdit("./Transfer")
        self.transfer_path_input.setStyleSheet(self.get_input_style())
        self.transfer_path_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        transfer_browse_btn = QPushButton("Browse")
        transfer_browse_btn.setFixedSize(70, 30)
        transfer_browse_btn.clicked.connect(self.browse_transfer_path)
        transfer_browse_btn.setStyleSheet(self.get_test_button_style())
        
        transfer_input_layout.addWidget(self.transfer_path_input)
        transfer_input_layout.addWidget(transfer_browse_btn)
        transfer_path_container.addLayout(transfer_input_layout)
        
        download_layout.addLayout(quality_layout)
        download_layout.addLayout(path_container)
        download_layout.addLayout(transfer_path_container)
        
        # Database Settings
        database_group = SettingsGroup("Database Settings")
        database_layout = QVBoxLayout(database_group)
        database_layout.setContentsMargins(16, 20, 16, 16)
        database_layout.setSpacing(12)
        
        # Max Workers
        workers_layout = QHBoxLayout()
        workers_label = QLabel("Concurrent Workers:")
        workers_label.setStyleSheet(self.get_label_style(12))
        
        self.max_workers_combo = QComboBox()
        self.max_workers_combo.addItems(["3", "4", "5", "6", "7", "8", "9", "10"])
        self.max_workers_combo.setCurrentText("5")  # Default value
        self.max_workers_combo.setStyleSheet(self.get_combo_style())
        self.max_workers_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        workers_layout.addWidget(workers_label)
        workers_layout.addWidget(self.max_workers_combo)
        
        # Help text for workers
        workers_help = QLabel("Number of parallel threads for database updates. Higher values = faster updates but more server load.")
        workers_help.setStyleSheet("color: #ffffff; font-size: 10px; font-style: italic; background: transparent;")
        workers_help.setWordWrap(True)
        
        database_layout.addLayout(workers_layout)
        database_layout.addWidget(workers_help)
        
        # Metadata Enhancement Settings
        metadata_group = SettingsGroup("🎵 Metadata Enhancement")
        metadata_layout = QVBoxLayout(metadata_group)
        metadata_layout.setContentsMargins(16, 20, 16, 16)
        metadata_layout.setSpacing(12)
        
        # Enable metadata enhancement checkbox
        self.metadata_enabled_checkbox = QCheckBox("Enable metadata enhancement with Spotify data")
        self.metadata_enabled_checkbox.setChecked(True)
        self.metadata_enabled_checkbox.setStyleSheet("""
            QCheckBox {
                color: #ffffff;
                font-size: 12px;
                spacing: 8px;
                background: transparent;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 3px;
                border: 2px solid #606060;
                background-color: #404040;
            }
            QCheckBox::indicator:checked {
                background-color: #1db954;
                border-color: #1db954;
                image: url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTYiIGhlaWdodD0iMTYiIHZpZXdCb3g9IjAgMCAxNiAxNiIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTEzLjUgNC41TDYuNSAxMS41TDIuNSA3LjUiIHN0cm9rZT0id2hpdGUiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+Cjwvc3ZnPgo=);
            }
            QCheckBox::indicator:hover {
                border-color: #1db954;
            }
        """)
        self.form_inputs['metadata_enhancement.enabled'] = self.metadata_enabled_checkbox
        
        # Embed album art checkbox
        self.embed_album_art_checkbox = QCheckBox("Embed high-quality album art from Spotify")
        self.embed_album_art_checkbox.setChecked(True)
        self.embed_album_art_checkbox.setStyleSheet(self.metadata_enabled_checkbox.styleSheet())
        self.form_inputs['metadata_enhancement.embed_album_art'] = self.embed_album_art_checkbox
        
        
        # Supported formats display
        supported_formats_layout = QHBoxLayout()
        formats_label = QLabel("Supported Formats:")
        formats_label.setStyleSheet(self.get_label_style(12))
        
        formats_display = QLabel("MP3, FLAC, MP4/M4A, OGG")
        formats_display.setStyleSheet("""
            color: #ffffff; 
            font-size: 11px; 
            background: transparent;
            border: none;
        """)
        
        supported_formats_layout.addWidget(formats_label)
        supported_formats_layout.addWidget(formats_display)
        
        # Help text
        help_text = QLabel("Automatically enhances downloaded tracks with accurate Spotify metadata including artist, album, track numbers, genres, and release dates. Perfect for Plex libraries!")
        help_text.setStyleSheet("color: #ffffff; font-size: 10px; font-style: italic; background: transparent;")
        help_text.setWordWrap(True)
        
        metadata_layout.addWidget(self.metadata_enabled_checkbox)
        metadata_layout.addWidget(self.embed_album_art_checkbox)
        metadata_layout.addLayout(supported_formats_layout)
        metadata_layout.addWidget(help_text)
        
        # Playlist Sync Settings
        playlist_sync_group = SettingsGroup("🎶 Playlist Sync")
        playlist_sync_layout = QVBoxLayout(playlist_sync_group)
        playlist_sync_layout.setContentsMargins(16, 20, 16, 16)
        playlist_sync_layout.setSpacing(12)
        
        # Create backup checkbox
        self.create_backup_checkbox = QCheckBox("🛡️ Create backup of existing playlists before sync")
        self.create_backup_checkbox.setChecked(True)
        self.create_backup_checkbox.setStyleSheet("""
            QCheckBox {
                color: #ffffff;
                font-size: 12px;
                spacing: 8px;
                background: transparent;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 3px;
                border: 2px solid #606060;
                background-color: #404040;
            }
            QCheckBox::indicator:checked {
                background-color: #1db954;
                border-color: #1db954;
                image: url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTYiIGhlaWdodD0iMTYiIHZpZXdCb3g9IjAgMCAxNiAxNiIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTEzLjUgNC41TDYuNSAxMS41TDIuNSA3LjUiIHN0cm9rZT0id2hpdGUiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+Cjwvc3ZnPgo=);
            }
            QCheckBox::indicator:hover {
                border-color: #1db954;
            }
        """)
        
        # Help text for playlist sync
        playlist_help_text = QLabel("When enabled, existing Plex playlists will be backed up as '[Playlist Name] Backup' before being overwritten during sync. Only one backup per playlist is maintained.")
        playlist_help_text.setStyleSheet("color: #ffffff; font-size: 10px; font-style: italic; background: transparent;")
        playlist_help_text.setWordWrap(True)
        
        playlist_sync_layout.addWidget(self.create_backup_checkbox)
        playlist_sync_layout.addWidget(playlist_help_text)
        
        # Add to form inputs for saving
        self.form_inputs['playlist_sync.create_backup'] = self.create_backup_checkbox

        # Logging Settings
        logging_group = SettingsGroup("Logging Settings")
        logging_layout = QVBoxLayout(logging_group)
        logging_layout.setContentsMargins(16, 20, 16, 16)
        logging_layout.setSpacing(12)
        
        # Log level (read-only)
        log_level_layout = QHBoxLayout()
        log_level_label = QLabel("Log Level:")
        log_level_label.setStyleSheet(self.get_label_style(12))
        
        self.log_level_display = QLabel("DEBUG")
        self.log_level_display.setStyleSheet("""
            color: #ffffff; 
            font-size: 11px; 
            background: transparent;
            border: none;
        """)
        self.log_level_display.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        log_level_layout.addWidget(log_level_label)
        log_level_layout.addWidget(self.log_level_display)
        
        # Log file path (read-only)
        log_path_container = QVBoxLayout()
        log_path_label = QLabel("Log File Path:")
        log_path_label.setStyleSheet(self.get_label_style(12))
        log_path_container.addWidget(log_path_label)
        
        self.log_path_display = QLabel("logs/app.log")
        self.log_path_display.setStyleSheet("""
            color: #1db954; 
            font-size: 11px; 
            font-family: 'Courier New', monospace;
            background-color: rgba(29, 185, 84, 0.1);
            border: 1px solid rgba(29, 185, 84, 0.3);
            border-radius: 4px;
            padding: 6px 8px;
        """)
        self.log_path_display.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        log_path_container.addWidget(self.log_path_display)
        
        logging_layout.addLayout(log_level_layout)
        logging_layout.addLayout(log_path_container)

        layout.addWidget(download_group)
        layout.addWidget(database_group)
        layout.addWidget(metadata_group)
        layout.addWidget(playlist_sync_group)
        layout.addWidget(logging_group)
        layout.addStretch()  # Push content to top, prevent stretching
        
        return column
    
    def get_input_style(self):
        return """
            QLineEdit {
                background: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                padding: 8px;
                color: #ffffff;
                font-size: 11px;
            }
            QLineEdit:focus {
                border: 1px solid #1db954;
            }
        """
    
    def select_media_server(self, server_type: str):
        """Handle media server selection toggle"""
        try:
            current_server = config_manager.get_active_media_server()
            
            if server_type != current_server:
                # Show restart warning
                self.restart_warning_frame.show()
                
                # Update the pending server change (but don't make it active yet)
                self.pending_server_change = server_type
            else:
                # Hide restart warning if selecting the current server
                self.restart_warning_frame.hide()
                self.pending_server_change = None
            
            # Update toggle button styles
            self.update_server_toggle_styles(server_type)
            
            # Show/hide appropriate containers
            if server_type == 'plex':
                self.plex_container.show()
                self.jellyfin_container.hide()
            else:
                self.plex_container.hide()
                self.jellyfin_container.show()
                
        except Exception as e:
            logger.error(f"Error selecting media server: {e}")
    
    def update_server_toggle_styles(self, active_server=None):
        """Update the visual styles of server toggle buttons"""
        if active_server is None:
            active_server = getattr(self, 'pending_server_change', None) or config_manager.get_active_media_server()
        
        from PyQt6.QtGui import QIcon, QPixmap
        from PyQt6.QtCore import QSize, Qt
        import requests
        import os
        from pathlib import Path
        
        def download_and_cache_logo(url, cache_filename, size=32):
            """Download logo and cache it locally, return QIcon"""
            cache_dir = Path("ui/assets")
            cache_dir.mkdir(exist_ok=True)
            cache_path = cache_dir / cache_filename
            
            # Download if not cached
            if not cache_path.exists():
                try:
                    logger.info(f"Downloading logo from {url}")
                    response = requests.get(url, timeout=10)
                    if response.status_code == 200:
                        with open(cache_path, 'wb') as f:
                            f.write(response.content)
                        logger.info(f"Logo cached at {cache_path}")
                    else:
                        logger.warning(f"Failed to download logo: HTTP {response.status_code}")
                        return QIcon()
                except Exception as e:
                    logger.warning(f"Error downloading logo from {url}: {e}")
                    return QIcon()
            
            # Load from cache
            try:
                pixmap = QPixmap(str(cache_path))
                if not pixmap.isNull():
                    # Scale to desired size while maintaining aspect ratio
                    scaled_pixmap = pixmap.scaled(
                        size, size, 
                        Qt.AspectRatioMode.KeepAspectRatio, 
                        Qt.TransformationMode.SmoothTransformation
                    )
                    return QIcon(scaled_pixmap)
                else:
                    logger.warning(f"Could not load cached logo from {cache_path}")
                    return QIcon()
            except Exception as e:
                logger.warning(f"Error loading cached logo: {e}")
                return QIcon()
        
        # Cache and load the exact logos you provided
        if not hasattr(self, '_cached_plex_icon'):
            self._cached_plex_icon = download_and_cache_logo(
                "https://wiki.mrmc.tv/images/c/cf/Plex_icon.png",
                "plex_icon.png",
                32
            )
        
        if not hasattr(self, '_cached_jellyfin_icon'):
            self._cached_jellyfin_icon = download_and_cache_logo(
                "https://upload.wikimedia.org/wikipedia/commons/thumb/4/41/Jellyfin_-_icon-transparent.svg/2048px-Jellyfin_-_icon-transparent.svg.png",
                "jellyfin_icon.png",
                32
            )
        
        plex_icon = self._cached_plex_icon
        jellyfin_icon = self._cached_jellyfin_icon
        
        # Active button styles with appropriate colors
        active_plex_style = """
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(229, 160, 13, 0.8),
                    stop:1 rgba(199, 140, 11, 0.9));
                border: 2px solid rgba(229, 160, 13, 1);
                border-radius: 8px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(229, 160, 13, 0.9),
                    stop:1 rgba(199, 140, 11, 1.0));
            }
        """
        
        active_jellyfin_style = """
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(170, 92, 195, 0.8),
                    stop:1 rgba(150, 82, 175, 0.9));
                border: 2px solid rgba(170, 92, 195, 1);
                border-radius: 8px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(170, 92, 195, 0.9),
                    stop:1 rgba(150, 82, 175, 1.0));
            }
        """
        
        # Inactive button style
        inactive_style = """
            QPushButton {
                background: transparent;
                border: 1px solid rgba(255, 255, 255, 0.3);
                border-radius: 8px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.1);
                border: 1px solid rgba(255, 255, 255, 0.5);
            }
        """
        
        # Set icons and styles
        self.plex_toggle_button.setIcon(plex_icon)
        self.plex_toggle_button.setIconSize(QSize(28, 28))
        self.jellyfin_toggle_button.setIcon(jellyfin_icon)
        self.jellyfin_toggle_button.setIconSize(QSize(28, 28))
        
        if active_server == 'plex':
            self.plex_toggle_button.setStyleSheet(active_plex_style)
            self.jellyfin_toggle_button.setStyleSheet(inactive_style)
        else:
            self.plex_toggle_button.setStyleSheet(inactive_style)
            self.jellyfin_toggle_button.setStyleSheet(active_jellyfin_style)
    
    def auto_detect_jellyfin(self):
        """Auto-detect Jellyfin server URL using background thread"""
        # Don't start new detection if one is already running
        if self.detection_thread and self.detection_thread.isRunning():
            return
        
        # Create animated loading dialog
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
        from PyQt6.QtCore import QTimer, QPropertyAnimation, QRect
        from PyQt6.QtGui import QPainter, QColor
        
        self.detection_dialog = QDialog(self)
        self.detection_dialog.setWindowTitle("Auto-detecting Jellyfin Server")
        self.detection_dialog.setModal(True)
        self.detection_dialog.setFixedSize(400, 180)
        self.detection_dialog.setWindowFlags(self.detection_dialog.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        
        # Apply dark theme styling
        self.detection_dialog.setStyleSheet("""
            QDialog {
                background-color: #282828;
                color: #ffffff;
                border: 1px solid #404040;
                border-radius: 8px;
            }
            QLabel {
                color: #ffffff;
                font-size: 14px;
            }
            QPushButton {
                background-color: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                color: #ffffff;
                padding: 8px 16px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
        """)
        
        layout = QVBoxLayout(self.detection_dialog)
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Title label
        title_label = QLabel("Searching for Jellyfin servers...")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        
        # Status label
        self.status_label = QLabel("Checking local machine...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #ffffff; font-size: 12px; background: transparent;")
        layout.addWidget(self.status_label)
        
        # Animated loading bar container
        loading_container = QLabel()
        loading_container.setFixedHeight(8)
        loading_container.setStyleSheet("""
            QLabel {
                background-color: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
            }
        """)
        layout.addWidget(loading_container)
        
        # Animated purple bar for Jellyfin
        self.loading_bar = QLabel(loading_container)
        self.loading_bar.setFixedHeight(6)
        self.loading_bar.setStyleSheet("""
            background-color: #aa5cc3;
            border-radius: 3px;
            border: none;
        """)
        
        # Start animation
        self.loading_animation = QPropertyAnimation(self.loading_bar, b"geometry")
        self.loading_animation.setDuration(1500)  # 1.5 seconds
        self.loading_animation.setStartValue(QRect(1, 1, 0, 6))
        self.loading_animation.setEndValue(QRect(1, 1, loading_container.width() - 2, 6))
        self.loading_animation.setLoopCount(-1)  # Infinite loop
        self.loading_animation.start()
        
        # Cancel button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.cancel_detection)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
        
        # Start Jellyfin detection thread
        self.detection_thread = JellyfinDetectionThread()
        self.detection_thread.progress_updated.connect(self.on_detection_progress, Qt.ConnectionType.QueuedConnection)
        self.detection_thread.detection_completed.connect(self.on_jellyfin_detection_completed, Qt.ConnectionType.QueuedConnection)
        self.detection_thread.start()
        
        self.detection_dialog.show()
    
    def on_jellyfin_detection_completed(self, found_url):
        """Handle Jellyfin detection completion"""
        # Stop animation and close dialog
        if hasattr(self, 'loading_animation'):
            self.loading_animation.stop()
        
        if hasattr(self, 'detection_dialog') and self.detection_dialog:
            self.detection_dialog.close()
            self.detection_dialog = None
        
        # Properly cleanup thread
        if self.detection_thread:
            if self.detection_thread.isRunning():
                self.detection_thread.quit()
                self.detection_thread.wait(1000)  # Wait up to 1 second for thread to finish
            self.detection_thread.deleteLater()
            self.detection_thread = None
        
        if found_url:
            self.jellyfin_url_input.setText(found_url)
            self.show_jellyfin_success_dialog(found_url)
            logger.info(f"Jellyfin auto-detection successful: {found_url}")
        else:
            from PyQt6.QtWidgets import QMessageBox
            msg = QMessageBox(self)
            msg.setWindowTitle("No Jellyfin Server Found")
            msg.setText("Could not find a Jellyfin server on your network.\n\nPlease enter your server URL manually (e.g., http://localhost:8096)")
            msg.setIcon(QMessageBox.Icon.Information)
            msg.exec()
            logger.info("Jellyfin auto-detection failed - no server found")
    
    
    def get_combo_style(self):
        return """
            QComboBox {
                background: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                padding: 8px;
                color: #ffffff;
                font-size: 11px;
                min-width: 100px;
            }
            QComboBox:focus {
                border: 1px solid #1db954;
            }
            QComboBox::drop-down {
                border: none;
            }
        """
    
    def get_spin_style(self):
        return """
            QSpinBox {
                background: #404040;
                border: 1px solid #606060;
                border-radius: 4px;
                padding: 8px;
                color: #ffffff;
                font-size: 11px;
                min-width: 80px;
            }
            QSpinBox:focus {
                border: 1px solid #1db954;
            }
        """
    
    def get_checkbox_style(self):
        return """
            QCheckBox {
                color: #ffffff;
                font-size: 12px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 8px;
                border: 2px solid #b3b3b3;
                background: transparent;
            }
            QCheckBox::indicator:checked {
                background: #1db954;
                border: 2px solid #1db954;
            }
        """
    
    def get_test_button_style(self):
        return """
            QPushButton {
                background: transparent;
                border: 1px solid #1db954;
                border-radius: 15px;
                color: #1db954;
                font-size: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(29, 185, 84, 0.1);
            }
        """
    
    def get_label_style(self, font_size=12):
        """Get consistent label style without background"""
        return f"""
            QLabel {{
                color: #ffffff;
                font-size: {font_size}px;
                background: transparent;
                border: none;
            }}
        """
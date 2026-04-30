"""Network detection — lifted from web_server.py.

Body is byte-identical to the original. Pure stdlib + requests, no
web_server-specific globals or runtime state.
"""
import ipaddress
import logging
import platform
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

logger = logging.getLogger(__name__)


def run_detection(server_type):
    """
    Performs comprehensive network detection for a given server type (plex, jellyfin, slskd).
    This implements the same scanning logic as the GUI's detection threads.
    """
    logger.info(f"Running comprehensive detection for {server_type}...")
    
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

    def test_jellyfin_server(ip, port=8096):
        """Test if a Jellyfin server is running at the given IP and port"""
        try:
            # Try the system info endpoint first
            url = f"http://{ip}:{port}/System/Info"
            response = requests.get(url, timeout=2, allow_redirects=True)
            
            if response.status_code == 200:
                # Check if response contains Jellyfin-specific content
                if 'jellyfin' in response.text.lower() or 'ServerName' in response.text:
                    return f"http://{ip}:{port}"
            
            # Also try the web interface
            web_url = f"http://{ip}:{port}/web/index.html"
            web_response = requests.get(web_url, timeout=1)
            if web_response.status_code == 200 and 'jellyfin' in web_response.text.lower():
                return f"http://{ip}:{port}"
                
        except:
            pass
        return None

    def test_slskd_server(ip, port=5030):
        """Test if a slskd server is running at the given IP and port"""
        try:
            # slskd specific API endpoint
            url = f"http://{ip}:{port}/api/v0/session"
            response = requests.get(url, timeout=2)
            
            # slskd returns 401 when not authenticated, which is still a valid response
            if response.status_code in [200, 401]:
                return f"http://{ip}:{port}"

        except:
            pass
        return None

    def test_navidrome_server(ip, port=4533):
        """Test if a Navidrome server is running at the given IP and port"""
        try:
            # Try Navidrome's ping endpoint (part of Subsonic API)
            url = f"http://{ip}:{port}/rest/ping"
            response = requests.get(url, timeout=2, params={
                'u': 'test',  # Dummy username for ping test
                'v': '1.16.1',  # API version
                'c': 'soulsync',  # Client name
                'f': 'json'  # Response format
            })

            # Navidrome should respond even with invalid credentials for ping
            if response.status_code in [200, 401, 403]:
                try:
                    data = response.json()
                    # Check for Subsonic/Navidrome API response structure
                    if 'subsonic-response' in data:
                        return f"http://{ip}:{port}"
                except:
                    pass

            # Also try the web interface
            web_url = f"http://{ip}:{port}/"
            web_response = requests.get(web_url, timeout=2)
            if web_response.status_code == 200 and 'navidrome' in web_response.text.lower():
                return f"http://{ip}:{port}"

        except:
            pass
        return None

    try:
        network_addr, netmask, local_ip, network = get_network_info()
        
        # Select the appropriate test function
        test_functions = {
            'plex': test_plex_server,
            'jellyfin': test_jellyfin_server,
            'navidrome': test_navidrome_server,
            'slskd': test_slskd_server
        }
        
        test_func = test_functions.get(server_type)
        if not test_func:
            return None
        
        # Priority 1: Test localhost first
        logger.debug(f"Testing localhost for {server_type}...")
        localhost_result = test_func("localhost")
        if localhost_result:
            logger.info(f"Found {server_type} at localhost!")
            return localhost_result
        
        # Priority 1.5: In Docker, try Docker host IP
        import os
        if os.path.exists('/.dockerenv'):
            logger.info(f"Docker detected, testing Docker host for {server_type}...")
            try:
                # Try host.docker.internal (Windows/Mac)
                host_result = test_func("host.docker.internal")
                if host_result:
                    logger.info(f"Found {server_type} at Docker host!")
                    return host_result.replace("host.docker.internal", "localhost")  # Convert back to localhost for config
                
                # Try Docker bridge gateway (Linux)
                gateway_result = test_func("172.17.0.1")
                if gateway_result:
                    logger.info(f"Found {server_type} at Docker gateway!")
                    return gateway_result.replace("172.17.0.1", "localhost")  # Convert back to localhost for config
            except Exception as e:
                logger.error(f"Docker host detection failed: {e}")
        
        # Priority 2: Test local IP
        logger.debug(f"Testing local IP {local_ip} for {server_type}...")
        local_result = test_func(local_ip)
        if local_result:
            logger.info(f"Found {server_type} at {local_ip}!")
            return local_result
        
        # Priority 3: Test common IPs (router gateway, etc.)
        common_ips = [
            local_ip.rsplit('.', 1)[0] + '.1',  # Typical gateway
            local_ip.rsplit('.', 1)[0] + '.2',  # Alternative gateway
            local_ip.rsplit('.', 1)[0] + '.100', # Common static IP
        ]
        
        logger.debug(f"Testing common IPs for {server_type}...")
        for ip in common_ips:
            logger.info(f"  Checking {ip}...")
            result = test_func(ip)
            if result:
                logger.info(f"Found {server_type} at {ip}!")
                return result
        
        # Priority 4: Scan the network range (limited to reasonable size)
        network_hosts = list(network.hosts())
        if len(network_hosts) > 50:
            # Limit scan to reasonable size for performance
            step = max(1, len(network_hosts) // 50)
            network_hosts = network_hosts[::step]
        
        logger.debug(f"Scanning network range for {server_type} ({len(network_hosts)} hosts)...")
        
        # Use ThreadPoolExecutor for concurrent scanning (limited for web context)
        with ThreadPoolExecutor(max_workers=5) as executor:
            # Submit all tasks
            future_to_ip = {executor.submit(test_func, str(ip)): str(ip) 
                           for ip in network_hosts}
            
            try:
                for future in as_completed(future_to_ip):
                    ip = future_to_ip[future]
                    try:
                        result = future.result()
                        if result:
                            logger.info(f"Found {server_type} at {ip}!")
                            # Cancel all pending futures before returning
                            for f in future_to_ip:
                                if not f.done():
                                    f.cancel()
                            return result
                    except Exception as e:
                        logger.error(f"Error testing {ip}: {e}")
                        continue
            except Exception as e:
                logger.error(f"Error in concurrent scanning: {e}")
        
        logger.warning(f"No {server_type} server found on network")
        return None
        
    except Exception as e:
        logger.error(f"Error during {server_type} detection: {e}")
        return None

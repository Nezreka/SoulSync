import requests
import asyncio
import aiohttp
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
import time
from pathlib import Path
from utils.logging_config import get_logger
from config.settings import config_manager

logger = get_logger("soulseek_client")

@dataclass
class SearchResult:
    username: str
    filename: str
    size: int
    bitrate: Optional[int]
    duration: Optional[int]
    quality: str
    free_upload_slots: int
    upload_speed: int
    queue_length: int
    
    @property
    def quality_score(self) -> float:
        quality_weights = {
            'flac': 1.0,
            'mp3': 0.8,
            'ogg': 0.7,
            'aac': 0.6,
            'wma': 0.5
        }
        
        base_score = quality_weights.get(self.quality.lower(), 0.3)
        
        if self.bitrate:
            if self.bitrate >= 320:
                base_score += 0.2
            elif self.bitrate >= 256:
                base_score += 0.1
            elif self.bitrate < 128:
                base_score -= 0.2
        
        if self.free_upload_slots > 0:
            base_score += 0.1
        
        if self.upload_speed > 100:
            base_score += 0.05
        
        if self.queue_length > 10:
            base_score -= 0.1
        
        return min(base_score, 1.0)

@dataclass
class DownloadStatus:
    id: str
    filename: str
    username: str
    state: str
    progress: float
    size: int
    transferred: int
    speed: int
    time_remaining: Optional[int] = None

class SoulseekClient:
    def __init__(self):
        self.base_url: Optional[str] = None
        self.api_key: Optional[str] = None
        self.download_path: Path = Path("./downloads")
        self._setup_client()
    
    def _setup_client(self):
        config = config_manager.get_soulseek_config()
        
        if not config.get('slskd_url'):
            logger.warning("Soulseek slskd URL not configured")
            return
        
        self.base_url = config['slskd_url'].rstrip('/')
        self.api_key = config.get('api_key', '')
        self.download_path = Path(config.get('download_path', './downloads'))
        self.download_path.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Soulseek client configured with slskd at {self.base_url}")
    
    def _get_headers(self) -> Dict[str, str]:
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            # Use X-API-Key authentication (Bearer tokens are session-based JWT tokens)
            headers['X-API-Key'] = self.api_key
        return headers
    
    async def _make_request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict[str, Any]]:
        if not self.base_url:
            logger.error("Soulseek client not configured")
            return None
        
        url = f"{self.base_url}/api/v0/{endpoint}"
        
        # Create a fresh session for each thread/event loop to avoid conflicts
        session = None
        try:
            session = aiohttp.ClientSession()
            
            headers = self._get_headers()
            logger.debug(f"Making {method} request to: {url}")
            logger.debug(f"Headers: {headers}")
            if 'json' in kwargs:
                logger.debug(f"JSON payload: {kwargs['json']}")
            
            async with session.request(
                method, 
                url, 
                headers=headers,
                **kwargs
            ) as response:
                response_text = await response.text()
                logger.debug(f"Response status: {response.status}")
                logger.debug(f"Response text: {response_text[:500]}...")  # First 500 chars
                
                if response.status in [200, 201]:  # Accept both 200 OK and 201 Created
                    try:
                        if response_text.strip():  # Only parse if there's content
                            return await response.json()
                        else:
                            # Return empty dict for successful requests with no content (like 201 Created)
                            return {}
                    except:
                        # If response_text was already consumed, parse it manually
                        import json
                        if response_text.strip():
                            return json.loads(response_text)
                        else:
                            return {}
                else:
                    logger.error(f"API request failed: {response.status} - {response_text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error making API request: {e}")
            return None
        finally:
            # Always clean up the session
            if session:
                try:
                    await session.close()
                except:
                    pass
    
    async def _make_direct_request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict[str, Any]]:
        """Make a direct request to slskd without /api/v0/ prefix (for endpoints that work directly)"""
        if not self.base_url:
            logger.error("Soulseek client not configured")
            return None
        
        url = f"{self.base_url}/{endpoint}"
        
        # Create a fresh session for each thread/event loop to avoid conflicts
        session = None
        try:
            session = aiohttp.ClientSession()
            
            headers = self._get_headers()
            logger.debug(f"Making direct {method} request to: {url}")
            logger.debug(f"Headers: {headers}")
            if 'json' in kwargs:
                logger.debug(f"JSON payload: {kwargs['json']}")
            
            async with session.request(
                method, 
                url, 
                headers=headers,
                **kwargs
            ) as response:
                response_text = await response.text()
                logger.debug(f"Response status: {response.status}")
                logger.debug(f"Response text: {response_text[:500]}...")  # First 500 chars
                
                if response.status == 200:
                    try:
                        return await response.json()
                    except:
                        # If response_text was already consumed, parse it manually
                        import json
                        return json.loads(response_text)
                else:
                    logger.error(f"Direct API request failed: {response.status} - {response_text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error making direct API request: {e}")
            return None
        finally:
            # Always clean up the session
            if session:
                try:
                    await session.close()
                except:
                    pass
    
    def _process_search_responses(self, responses_data: List[Dict[str, Any]]) -> List[SearchResult]:
        """Process search response data into SearchResult objects"""
        search_results = []
        
        logger.debug(f"Processing {len(responses_data)} user responses")
        
        for response_data in responses_data:
            username = response_data.get('username', '')
            files = response_data.get('files', [])
            logger.debug(f"User {username} has {len(files)} files")
            
            for file_data in files:
                filename = file_data.get('filename', '')
                size = file_data.get('size', 0)
                
                file_ext = Path(filename).suffix.lower().lstrip('.')
                quality = file_ext if file_ext in ['flac', 'mp3', 'ogg', 'aac', 'wma'] else 'unknown'
                
                result = SearchResult(
                    username=username,
                    filename=filename,
                    size=size,
                    bitrate=file_data.get('bitRate'),
                    duration=file_data.get('length'),
                    quality=quality,
                    free_upload_slots=response_data.get('freeUploadSlots', 0),
                    upload_speed=response_data.get('uploadSpeed', 0),
                    queue_length=response_data.get('queueLength', 0)
                )
                search_results.append(result)
        
        return search_results
    
    async def search(self, query: str, timeout: int = 30, progress_callback=None) -> List[SearchResult]:
        if not self.base_url:
            logger.error("Soulseek client not configured")
            return []
        
        try:
            logger.info(f"Starting search for: '{query}'")
            
            search_data = {
                'searchText': query,
                'timeout': timeout * 1000,  # slskd expects milliseconds
                'filterResponses': True,
                'minimumResponseFileCount': 1,
                'minimumPeerUploadSpeed': 0
            }
            
            logger.debug(f"Search data: {search_data}")
            logger.debug(f"Making POST request to: {self.base_url}/api/v0/searches")
            
            response = await self._make_request('POST', 'searches', json=search_data)
            if not response:
                logger.error("No response from search POST request")
                return []
            
            search_id = response.get('id')
            if not search_id:
                logger.error("No search ID returned from POST request")
                logger.debug(f"Full response: {response}")
                return []
            
            logger.info(f"Search initiated with ID: {search_id}")
            
            # Poll for results instead of blocking sleep - like web interface does
            all_results = []
            poll_interval = 1.5  # Check every 1.5 seconds for more responsive updates
            max_polls = int(timeout / poll_interval)  # 20 attempts over 30 seconds
            
            for poll_count in range(max_polls):
                logger.debug(f"Polling for results (attempt {poll_count + 1}/{max_polls}) - elapsed: {poll_count * poll_interval:.1f}s")
                
                # Get current search responses
                responses_data = await self._make_request('GET', f'searches/{search_id}/responses')
                if responses_data and isinstance(responses_data, list):
                    current_results = self._process_search_responses(responses_data)
                    
                    # Add new unique results
                    existing_filenames = {r.filename for r in all_results}
                    new_results = [r for r in current_results if r.filename not in existing_filenames]
                    all_results.extend(new_results)
                    
                    if new_results:
                        logger.info(f"Found {len(new_results)} new results (total: {len(all_results)}) at {poll_count * poll_interval:.1f}s")
                        # Call progress callback with new results
                        if progress_callback:
                            try:
                                progress_callback(new_results, len(all_results))
                            except Exception as e:
                                logger.error(f"Error in progress callback: {e}")
                        
                        # Early termination if we have enough results
                        if len(all_results) >= 100:  # Stop after 100 results for better performance
                            logger.info(f"Early termination: Found {len(all_results)} results, stopping search")
                            break
                    elif len(all_results) > 0:
                        logger.debug(f"No new results, total still: {len(all_results)}")
                    else:
                        logger.debug(f"Still waiting for results... ({poll_count * poll_interval:.1f}s elapsed)")
                
                # Wait before next poll (unless this is the last attempt)
                if poll_count < max_polls - 1:
                    await asyncio.sleep(poll_interval)
            
            logger.info(f"Search completed. Found {len(all_results)} total results for query: {query}")
            
            # Sort by quality score and return
            all_results.sort(key=lambda x: x.quality_score, reverse=True)
            return all_results
            
        except Exception as e:
            logger.error(f"Error searching: {e}")
            return []
    
    async def download(self, username: str, filename: str, file_size: int = 0) -> Optional[str]:
        if not self.base_url:
            logger.error("Soulseek client not configured")
            return None
        
        try:
            logger.debug(f"Attempting to download: {filename} from {username} (size: {file_size})")
            
            # Use the exact format observed in the web interface
            # Payload: [{filename: "...", size: 123}] - array of files
            # Try adding path parameter to see if slskd supports custom download paths
            download_data = [
                {
                    "filename": filename,
                    "size": file_size,
                    "path": str(self.download_path)  # Try custom download path
                }
            ]
            
            logger.debug(f"Using web interface API format: {download_data}")
            
            # Use the correct endpoint pattern from web interface: /api/v0/transfers/downloads/{username}
            endpoint = f'transfers/downloads/{username}'
            logger.debug(f"Trying web interface endpoint: {endpoint}")
            
            try:
                response = await self._make_request('POST', endpoint, json=download_data)
                if response is not None:  # 201 Created returns empty dict {} but status 201
                    logger.info(f"[SUCCESS] Started download: {filename} from {username}")
                    return filename
                else:
                    logger.debug(f"Web interface endpoint returned no response")
                    
            except Exception as e:
                logger.debug(f"Web interface endpoint failed: {e}")
            
            # Fallback: Try alternative patterns if the main one fails
            logger.debug("Web interface endpoint failed, trying alternatives...")
            
            # Try different username-based endpoint patterns
            username_endpoints_to_try = [
                f'transfers/{username}/enqueue',
                f'users/{username}/downloads', 
                f'users/{username}/enqueue'
            ]
            
            # Try with array format first
            for endpoint in username_endpoints_to_try:
                logger.debug(f"Trying endpoint: {endpoint} with array format")
                
                try:
                    response = await self._make_request('POST', endpoint, json=download_data)
                    if response is not None:
                        logger.info(f"[SUCCESS] Started download: {filename} from {username} using endpoint: {endpoint}")
                        return filename
                    else:
                        logger.debug(f"Endpoint {endpoint} returned no response")
                        
                except Exception as e:
                    logger.debug(f"Endpoint {endpoint} failed: {e}")
                    continue
            
            # Try with old format as final fallback
            logger.debug("Array format failed, trying old object format")
            fallback_data = {
                "files": [
                    {
                        "filename": filename,
                        "size": file_size
                    }
                ]
            }
            
            for endpoint in username_endpoints_to_try:
                logger.debug(f"Trying endpoint: {endpoint} with object format")
                
                try:
                    response = await self._make_request('POST', endpoint, json=fallback_data)
                    if response is not None:
                        logger.info(f"[SUCCESS] Started download: {filename} from {username} using fallback endpoint: {endpoint}")
                        return filename
                    else:
                        logger.debug(f"Fallback endpoint {endpoint} returned no response")
                        
                except Exception as e:
                    logger.debug(f"Fallback endpoint {endpoint} failed: {e}")
                    continue
            
            logger.error(f"All download endpoints failed for {filename} from {username}")
            return None
            
        except Exception as e:
            logger.error(f"Error starting download: {e}")
            return None
    
    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        if not self.base_url:
            return None
        
        try:
            response = await self._make_request('GET', f'transfers/downloads/{download_id}')
            if not response:
                return None
            
            return DownloadStatus(
                id=response.get('id', ''),
                filename=response.get('filename', ''),
                username=response.get('username', ''),
                state=response.get('state', ''),
                progress=response.get('percentComplete', 0.0),
                size=response.get('size', 0),
                transferred=response.get('bytesTransferred', 0),
                speed=response.get('averageSpeed', 0),
                time_remaining=response.get('timeRemaining')
            )
            
        except Exception as e:
            logger.error(f"Error getting download status: {e}")
            return None
    
    async def get_all_downloads(self) -> List[DownloadStatus]:
        if not self.base_url:
            return []
        
        try:
            # Try different endpoints for getting downloads
            response = await self._make_request('GET', 'downloads')
            if not response:
                # Fallback to the old endpoint
                response = await self._make_request('GET', 'transfers/downloads')
                
            if not response:
                return []
            
            downloads = []
            for download_data in response:
                status = DownloadStatus(
                    id=download_data.get('id', ''),
                    filename=download_data.get('filename', ''),
                    username=download_data.get('username', ''),
                    state=download_data.get('state', ''),
                    progress=download_data.get('percentComplete', 0.0),
                    size=download_data.get('size', 0),
                    transferred=download_data.get('bytesTransferred', 0),
                    speed=download_data.get('averageSpeed', 0),
                    time_remaining=download_data.get('timeRemaining')
                )
                downloads.append(status)
            
            return downloads
            
        except Exception as e:
            logger.error(f"Error getting downloads: {e}")
            return []
    
    async def cancel_download(self, download_id: str) -> bool:
        if not self.base_url:
            return False
        
        try:
            response = await self._make_request('DELETE', f'transfers/downloads/{download_id}')
            return response is not None
            
        except Exception as e:
            logger.error(f"Error cancelling download: {e}")
            return False
    
    async def search_and_download_best(self, query: str, preferred_quality: str = 'flac') -> Optional[str]:
        results = await self.search(query)
        
        if not results:
            logger.warning(f"No results found for: {query}")
            return None
        
        preferred_results = [r for r in results if r.quality.lower() == preferred_quality.lower()]
        
        if preferred_results:
            best_result = preferred_results[0]
        else:
            best_result = results[0]
            logger.info(f"Preferred quality {preferred_quality} not found, using {best_result.quality}")
        
        logger.info(f"Downloading: {best_result.filename} ({best_result.quality}) from {best_result.username}")
        return await self.download(best_result.username, best_result.filename, best_result.size)
    
    async def check_connection(self) -> bool:
        """Check if slskd is running and accessible"""
        if not self.base_url:
            return False
        
        try:
            response = await self._make_request('GET', 'session')
            return response is not None
        except Exception as e:
            logger.debug(f"Connection check failed: {e}")
            return False
    
    async def get_session_info(self) -> Optional[Dict[str, Any]]:
        """Get slskd session information including version"""
        if not self.base_url:
            return None
        
        try:
            response = await self._make_request('GET', 'session')
            if response:
                logger.info(f"slskd session info: {response}")
                return response
            return None
        except Exception as e:
            logger.error(f"Error getting session info: {e}")
            return None
    
    async def explore_api_endpoints(self) -> Dict[str, Any]:
        """Explore available API endpoints to find the correct download endpoint"""
        if not self.base_url:
            return {}
        
        try:
            logger.info("Exploring slskd API endpoints...")
            
            # Try to get Swagger/OpenAPI documentation
            swagger_url = f"{self.base_url}/swagger/v1/swagger.json"
            
            session = aiohttp.ClientSession()
            try:
                headers = self._get_headers()
                async with session.get(swagger_url, headers=headers) as response:
                    if response.status == 200:
                        swagger_data = await response.json()
                        logger.info("✓ Found Swagger documentation")
                        
                        # Look for download/transfer related endpoints
                        paths = swagger_data.get('paths', {})
                        download_endpoints = {}
                        
                        for path, methods in paths.items():
                            if any(keyword in path.lower() for keyword in ['download', 'transfer', 'enqueue']):
                                download_endpoints[path] = methods
                                logger.info(f"Found endpoint: {path} with methods: {list(methods.keys())}")
                        
                        return {
                            'swagger_available': True,
                            'download_endpoints': download_endpoints,
                            'base_url': self.base_url
                        }
                    else:
                        logger.debug(f"Swagger endpoint returned {response.status}")
            except Exception as e:
                logger.debug(f"Could not access Swagger docs: {e}")
            finally:
                await session.close()
            
            # If Swagger is not available, try common endpoints manually
            logger.info("Swagger not available, testing common endpoints...")
            
            common_endpoints = [
                'transfers',
                'downloads', 
                'transfers/downloads',
                'api/transfers',
                'api/downloads'
            ]
            
            available_endpoints = {}
            
            for endpoint in common_endpoints:
                try:
                    response = await self._make_request('GET', endpoint)
                    if response is not None:
                        available_endpoints[endpoint] = 'GET available'
                        logger.info(f"[OK] Endpoint available: {endpoint}")
                    else:
                        # Try different endpoints without /api/v0 prefix
                        simple_url = f"{self.base_url}/{endpoint}"
                        session = aiohttp.ClientSession()
                        try:
                            headers = self._get_headers()
                            async with session.get(simple_url, headers=headers) as resp:
                                if resp.status in [200, 405]:  # 405 means endpoint exists but wrong method
                                    available_endpoints[f"direct_{endpoint}"] = f"Status: {resp.status}"
                                    logger.info(f"[OK] Direct endpoint available: {simple_url} (Status: {resp.status})")
                        except:
                            pass
                        finally:
                            await session.close()
                            
                except Exception as e:
                    logger.debug(f"Endpoint {endpoint} failed: {e}")
            
            return {
                'swagger_available': False,
                'available_endpoints': available_endpoints,
                'base_url': self.base_url
            }
            
        except Exception as e:
            logger.error(f"Error exploring API endpoints: {e}")
            return {'error': str(e)}
    
    def is_configured(self) -> bool:
        """Check if slskd is configured (has base_url)"""
        return self.base_url is not None
    
    async def close(self):
        # No persistent session to close - each request creates its own session
        pass
    
    def __del__(self):
        # No persistent session to clean up
        pass
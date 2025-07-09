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
        self.session: Optional[aiohttp.ClientSession] = None
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
            headers['X-API-Key'] = self.api_key
        return headers
    
    async def _make_request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict[str, Any]]:
        if not self.base_url:
            logger.error("Soulseek client not configured")
            return None
        
        url = f"{self.base_url}/api/v0/{endpoint}"
        
        if not self.session:
            self.session = aiohttp.ClientSession()
        
        try:
            async with self.session.request(
                method, 
                url, 
                headers=self._get_headers(),
                **kwargs
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.error(f"API request failed: {response.status} - {await response.text()}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error making API request: {e}")
            return None
    
    async def search(self, query: str, timeout: int = 30) -> List[SearchResult]:
        if not self.base_url:
            logger.error("Soulseek client not configured")
            return []
        
        try:
            search_data = {
                'searchText': query,
                'timeout': timeout * 1000,
                'filterResponses': True,
                'minimumResponseFileCount': 1,
                'minimumPeerUploadSpeed': 0
            }
            
            response = await self._make_request('POST', 'searches', json=search_data)
            if not response:
                return []
            
            search_id = response.get('id')
            if not search_id:
                logger.error("No search ID returned")
                return []
            
            await asyncio.sleep(timeout)
            
            results_response = await self._make_request('GET', f'searches/{search_id}')
            if not results_response:
                return []
            
            search_results = []
            for response_data in results_response.get('responses', []):
                username = response_data.get('username', '')
                
                for file_data in response_data.get('files', []):
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
            
            search_results.sort(key=lambda x: x.quality_score, reverse=True)
            logger.info(f"Found {len(search_results)} results for query: {query}")
            return search_results
            
        except Exception as e:
            logger.error(f"Error searching: {e}")
            return []
    
    async def download(self, username: str, filename: str) -> Optional[str]:
        if not self.base_url:
            logger.error("Soulseek client not configured")
            return None
        
        try:
            download_data = {
                'username': username,
                'files': [filename]
            }
            
            response = await self._make_request('POST', 'transfers/downloads', json=download_data)
            if response:
                logger.info(f"Started download: {filename} from {username}")
                return filename
            
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
        return await self.download(best_result.username, best_result.filename)
    
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
    
    def is_configured(self) -> bool:
        """Check if slskd is configured (has base_url)"""
        return self.base_url is not None
    
    async def close(self):
        if self.session:
            await self.session.close()
    
    def __del__(self):
        if self.session and not self.session.closed:
            try:
                asyncio.get_event_loop().run_until_complete(self.session.close())
            except:
                pass
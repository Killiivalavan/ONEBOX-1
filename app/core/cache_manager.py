from typing import Any, Dict, Optional, List, Tuple
import time
from datetime import datetime
import asyncio
import logging
from collections import OrderedDict
from .memory_monitor import memory_monitor, MemoryStats

logger = logging.getLogger(__name__)

class LRUCache:
    def __init__(self, max_size: int = 1000):
        self.cache = OrderedDict()
        self.max_size = max_size
        
    def get(self, key: str) -> Optional[Any]:
        if key not in self.cache:
            return None
        self.cache.move_to_end(key)
        return self.cache[key]
        
    def put(self, key: str, value: Any):
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)
            
    def remove(self, key: str):
        if key in self.cache:
            del self.cache[key]
            
    def clear(self):
        self.cache.clear()

class CacheManager:
    def __init__(self,
                 max_memory_percent: float = 20.0,
                 check_interval: int = 60,
                 ttl: int = 300):
        self._max_memory_percent = max_memory_percent
        self._check_interval = check_interval
        self._ttl = ttl
        self._logger = logging.getLogger(__name__)
        self._setup_logging()
        
        # Separate caches for different types of data
        self._email_cache = LRUCache(max_size=1000)
        self._search_cache = LRUCache(max_size=500)
        self._metadata_cache = LRUCache(max_size=2000)
        
        # Cache statistics
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        
        # Register with memory monitor
        memory_monitor.register_callback("cache_manager", self.handle_memory_warning)
        
    def _setup_logging(self):
        handler = logging.FileHandler('cache_manager.log')
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self._logger.addHandler(handler)
        self._logger.setLevel(logging.INFO)
        
    async def get_email(self, email_id: str) -> Optional[Dict]:
        """Get email from cache"""
        result = self._email_cache.get(email_id)
        if result:
            self._hits += 1
            if self._is_expired(result['timestamp']):
                self._email_cache.remove(email_id)
                self._misses += 1
                return None
            return result['data']
        self._misses += 1
        return None
        
    async def set_email(self, email_id: str, data: Dict):
        """Cache email data"""
        self._email_cache.put(email_id, {
            'data': data,
            'timestamp': time.time()
        })
        
    async def get_search_results(self, query_key: str) -> Optional[Dict]:
        """Get search results from cache"""
        result = self._search_cache.get(query_key)
        if result:
            self._hits += 1
            if self._is_expired(result['timestamp']):
                self._search_cache.remove(query_key)
                self._misses += 1
                return None
            return result['data']
        self._misses += 1
        return None
        
    async def set_search_results(self, query_key: str, data: Dict):
        """Cache search results"""
        self._search_cache.put(query_key, {
            'data': data,
            'timestamp': time.time()
        })
        
    def _is_expired(self, timestamp: float) -> bool:
        """Check if cache entry is expired"""
        return time.time() - timestamp > self._ttl
        
    async def clear_expired(self):
        """Clear expired cache entries"""
        current_time = time.time()
        
        # Clear expired email cache entries
        expired_emails = [
            key for key, value in self._email_cache.cache.items()
            if self._is_expired(value['timestamp'])
        ]
        for key in expired_emails:
            self._email_cache.remove(key)
            self._evictions += 1
            
        # Clear expired search cache entries
        expired_searches = [
            key for key, value in self._search_cache.cache.items()
            if self._is_expired(value['timestamp'])
        ]
        for key in expired_searches:
            self._search_cache.remove(key)
            self._evictions += 1
            
        self._logger.info(f"Cleared {len(expired_emails)} expired email cache entries")
        self._logger.info(f"Cleared {len(expired_searches)} expired search cache entries")
        
    async def handle_memory_warning(self, stats: MemoryStats):
        """Handle memory warning by clearing caches"""
        if stats.memory_percent > self._max_memory_percent:
            self._logger.warning(f"Memory usage ({stats.memory_percent}%) exceeded threshold ({self._max_memory_percent}%)")
            # Clear all caches
            self._email_cache.clear()
            self._search_cache.clear()
            self._metadata_cache.clear()
            self._logger.info("Cleared all caches due to memory pressure")
            
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        return {
            'hits': self._hits,
            'misses': self._misses,
            'evictions': self._evictions,
            'email_cache_size': len(self._email_cache.cache),
            'search_cache_size': len(self._search_cache.cache),
            'metadata_cache_size': len(self._metadata_cache.cache)
        }
        
    async def start_cleanup_task(self):
        """Start periodic cleanup task"""
        while True:
            try:
                await self.clear_expired()
                await asyncio.sleep(self._check_interval)
            except Exception as e:
                self._logger.error(f"Error in cleanup task: {e}")
                await asyncio.sleep(self._check_interval)

# Global instance
cache_manager = CacheManager() 
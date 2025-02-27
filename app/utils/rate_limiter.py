import time
import asyncio
from typing import Dict, Optional, Deque
from collections import deque
from datetime import datetime, timedelta

class RateLimiter:
    """
    Sliding window rate limiter implementation.
    Tracks actual request timestamps for more precise per-minute limiting.
    """
    
    def __init__(self, tokens_per_second: float, bucket_size: int):
        self.tokens_per_second = tokens_per_second
        self.requests_per_minute = int(tokens_per_second * 60)
        self.bucket_size = bucket_size
        self._lock = asyncio.Lock()
        self.request_timestamps: Deque[float] = deque(maxlen=self.requests_per_minute)
    
    async def acquire(self) -> bool:
        """
        Acquire a token. Returns True if a token was acquired, False otherwise.
        Uses a sliding window to track requests in the last minute.
        """
        async with self._lock:
            now = time.time()
            minute_ago = now - 60
            
            # Remove timestamps older than 1 minute
            while self.request_timestamps and self.request_timestamps[0] < minute_ago:
                self.request_timestamps.popleft()
            
            # Check if we're under the rate limit
            if len(self.request_timestamps) < self.requests_per_minute:
                self.request_timestamps.append(now)
                return True
                
            return False
    
    async def wait_for_token(self, timeout: Optional[float] = None) -> bool:
        """
        Wait until a token is available or timeout is reached.
        Returns True if token was acquired, False if timeout was reached.
        """
        start_time = time.time()
        while True:
            if await self.acquire():
                return True
                
            if timeout is not None and time.time() - start_time > timeout:
                return False
            
            # Calculate time until next token might be available
            async with self._lock:
                if self.request_timestamps:
                    # Wait until the oldest request expires
                    sleep_time = max(0.1, self.request_timestamps[0] + 60 - time.time())
                    await asyncio.sleep(min(sleep_time, 1.0))  # Cap at 1 second to prevent too long sleeps
                else:
                    await asyncio.sleep(0.1)  # Fallback delay 
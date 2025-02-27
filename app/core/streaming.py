from typing import AsyncGenerator, Any, Optional
import asyncio
from fastapi import HTTPException
from starlette.responses import StreamingResponse
import json
import logging
from .memory_monitor import memory_monitor, MemoryStats

logger = logging.getLogger(__name__)

class StreamingManager:
    def __init__(self, chunk_size: int = 8192):
        self.chunk_size = chunk_size
        self._logger = logging.getLogger(__name__)
        self._setup_logging()
        
    def _setup_logging(self):
        handler = logging.FileHandler('streaming.log')
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self._logger.addHandler(handler)
        self._logger.setLevel(logging.INFO)

    async def stream_large_content(self, content: str) -> AsyncGenerator[bytes, Any]:
        """Stream large content in chunks"""
        try:
            # Convert content to bytes if it's a string
            if isinstance(content, str):
                content = content.encode('utf-8')
            
            # Stream in chunks
            for i in range(0, len(content), self.chunk_size):
                chunk = content[i:i + self.chunk_size]
                yield chunk
                # Small delay to prevent memory spikes
                await asyncio.sleep(0.01)
                
        except Exception as e:
            self._logger.error(f"Error streaming content: {e}")
            raise HTTPException(status_code=500, detail="Streaming error")

    async def stream_email_response(self, email_data: dict) -> StreamingResponse:
        """Create a streaming response for email data"""
        try:
            # Extract content for streaming
            content = email_data.pop('content', '')
            
            # Create header with metadata
            header = {
                'metadata': email_data,
                'content_length': len(content) if content else 0
            }
            
            async def content_generator():
                # First yield metadata
                yield json.dumps(header).encode('utf-8') + b'\n'
                
                # Then stream content if present
                if content:
                    async for chunk in self.stream_large_content(content):
                        yield chunk
            
            return StreamingResponse(
                content_generator(),
                media_type='application/json'
            )
            
        except Exception as e:
            self._logger.error(f"Error creating streaming response: {e}")
            raise HTTPException(status_code=500, detail="Streaming error")

    async def handle_memory_warning(self, stats: MemoryStats):
        """Handle memory warning by adjusting chunk size"""
        if stats.memory_percent > 80:
            self.chunk_size = max(1024, self.chunk_size // 2)
            self._logger.warning(f"Reduced chunk size to {self.chunk_size} due to high memory usage")
        elif stats.memory_percent < 50:
            self.chunk_size = min(16384, self.chunk_size * 2)
            self._logger.info(f"Increased chunk size to {self.chunk_size} due to low memory usage")

# Global instance
streaming_manager = StreamingManager()

# Register memory warning callback
memory_monitor.register_callback("streaming_manager", streaming_manager.handle_memory_warning) 
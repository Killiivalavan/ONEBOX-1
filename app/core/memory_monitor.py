import psutil
import gc
import logging
import asyncio
from typing import Optional, Dict, Callable
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class MemoryStats:
    total_memory: int
    available_memory: int
    used_memory: int
    memory_percent: float
    timestamp: datetime

class MemoryMonitor:
    def __init__(self, 
                 warning_threshold: float = 70.0,
                 critical_threshold: float = 85.0,
                 check_interval: int = 30):
        self._warning_threshold = warning_threshold
        self._critical_threshold = critical_threshold
        self._check_interval = check_interval
        self._monitoring_task: Optional[asyncio.Task] = None
        self._callbacks: Dict[str, Callable] = {}
        self._last_gc_time = datetime.now()
        self._gc_interval = 300  # 5 minutes
        self._logger = logging.getLogger(__name__)
        self._setup_logging()

    def _setup_logging(self):
        handler = logging.FileHandler('memory_monitor.log')
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self._logger.addHandler(handler)
        self._logger.setLevel(logging.INFO)

    def get_memory_stats(self) -> MemoryStats:
        process = psutil.Process()
        system = psutil.virtual_memory()
        
        return MemoryStats(
            total_memory=system.total,
            available_memory=system.available,
            used_memory=process.memory_info().rss,
            memory_percent=process.memory_percent(),
            timestamp=datetime.now()
        )

    def register_callback(self, name: str, callback: Callable):
        """Register a callback to be called when memory thresholds are exceeded"""
        self._callbacks[name] = callback

    def unregister_callback(self, name: str):
        """Unregister a callback"""
        if name in self._callbacks:
            del self._callbacks[name]

    async def _check_memory(self):
        """Check memory usage and take action if necessary"""
        stats = self.get_memory_stats()
        self._logger.debug(f"Memory stats: {stats}")

        if stats.memory_percent > self._critical_threshold:
            self._logger.warning(f"Critical memory usage: {stats.memory_percent}%")
            # Force garbage collection
            gc.collect()
            # Call all registered callbacks
            for name, callback in self._callbacks.items():
                try:
                    await callback(stats)
                except Exception as e:
                    self._logger.error(f"Error in callback {name}: {e}")

        elif stats.memory_percent > self._warning_threshold:
            self._logger.warning(f"High memory usage: {stats.memory_percent}%")
            # Check if it's time for garbage collection
            if (datetime.now() - self._last_gc_time).total_seconds() > self._gc_interval:
                gc.collect()
                self._last_gc_time = datetime.now()

    async def _monitor_loop(self):
        """Main monitoring loop"""
        while True:
            try:
                await self._check_memory()
                await asyncio.sleep(self._check_interval)
            except Exception as e:
                self._logger.error(f"Error in memory monitor loop: {e}")
                await asyncio.sleep(self._check_interval)

    async def start(self):
        """Start the memory monitor"""
        if self._monitoring_task is None:
            self._logger.info("Starting memory monitor")
            self._monitoring_task = asyncio.create_task(self._monitor_loop())

    async def stop(self):
        """Stop the memory monitor"""
        if self._monitoring_task:
            self._logger.info("Stopping memory monitor")
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass
            self._monitoring_task = None

# Global instance
memory_monitor = MemoryMonitor() 
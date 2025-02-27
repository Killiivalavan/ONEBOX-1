from elasticsearch import AsyncElasticsearch, NotFoundError, ConnectionError, TransportError
from datetime import datetime, timedelta
from typing import (
    List, Dict, Optional, Any, Tuple, AsyncContextManager,
    AsyncGenerator, TypeVar, Protocol, runtime_checkable,
    AsyncIterator
)
from contextlib import asynccontextmanager
from ..core.config import settings
from ..models.email import Email
from ..core.interfaces import SearchServiceProtocol
from ..core.database import AsyncSessionLocal
import asyncio
import logging
import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from sqlalchemy.exc import SQLAlchemyError
import time
from functools import lru_cache
import aiocache
from collections import defaultdict
import psutil
import gc

logger = logging.getLogger(__name__)

T = TypeVar('T')

@runtime_checkable
class AsyncContextManagerProtocol(Protocol[T]):
    async def __aenter__(self) -> T: ...
    async def __aexit__(self, exc_type: Optional[type], exc_val: Optional[Exception], exc_tb: Any) -> Optional[bool]: ...

class SearchError(Exception):
    """Base class for search-related errors"""
    pass

class IndexingError(SearchError):
    """Error during indexing operations"""
    pass

class SearchTimeoutError(SearchError):
    """Error when search operation times out"""
    pass

class OperationMetrics:
    """Class to hold operation metrics"""
    def __init__(self, operation_name: str):
        # Basic timing metrics
        self.operation_name = operation_name
        self.start_time = time.perf_counter()
        self.end_time: Optional[float] = None
        self.duration: Optional[float] = None
        
        # Operation status
        self.success: bool = False
        self.error: Optional[Exception] = None
        self.error_type: Optional[str] = None
        self.error_details: Optional[str] = None
        
        # Memory metrics
        self.memory_before = psutil.Process().memory_info().rss
        self.memory_after: Optional[int] = None
        self.memory_diff: Optional[int] = None
        
        # Search-specific metrics
        self.search_hits: Optional[int] = None
        self.search_took: Optional[int] = None
        self.result_count: Optional[int] = None
        self.cache_hit: bool = False
        self.used_fallback: bool = False
        self.query_complexity: Optional[int] = None
        
        # Cache metrics
        self.cache_size: Optional[int] = None
        self.cache_memory: Optional[int] = None
        
        # Performance flags
        self.timeout_occurred: bool = False
        self.gc_triggered: bool = False
        
        # Timestamp
        self.timestamp = datetime.now()

    def complete(self, success: bool = True, error: Optional[Exception] = None):
        """Complete the operation and calculate metrics"""
        self.end_time = time.perf_counter()
        self.duration = self.end_time - self.start_time
        self.success = success
        self.error = error
        
        if error:
            self.error_type = type(error).__name__
            self.error_details = str(error)
        
        self.memory_after = psutil.Process().memory_info().rss
        self.memory_diff = self.memory_after - self.memory_before

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary"""
        return {
            # Basic info
            "operation_name": self.operation_name,
            "timestamp": self.timestamp.isoformat(),
            
            # Timing
            "duration_seconds": self.duration,
            "start_time": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.start_time)),
            
            # Status
            "success": self.success,
            "error_type": self.error_type,
            "error_details": self.error_details,
            
            # Memory
            "memory_before_bytes": self.memory_before,
            "memory_after_bytes": self.memory_after,
            "memory_diff_bytes": self.memory_diff,
            
            # Search metrics
            "search_hits": self.search_hits,
            "search_took_ms": self.search_took,
            "result_count": self.result_count,
            "cache_hit": self.cache_hit,
            "used_fallback": self.used_fallback,
            "query_complexity": self.query_complexity,
            
            # Cache metrics
            "cache_size": self.cache_size,
            "cache_memory_bytes": self.cache_memory,
            
            # Performance flags
            "timeout_occurred": self.timeout_occurred,
            "gc_triggered": self.gc_triggered
        }

    def log_summary(self, logger: logging.Logger):
        """Log a summary of the metrics"""
        summary = (
            f"\nOperation Metrics Summary for '{self.operation_name}':\n"
            f"  Duration: {self.duration:.2f}s\n"
            f"  Success: {self.success}\n"
            f"  Memory Impact: {self.memory_diff / 1024 / 1024:.2f}MB\n"
        )
        
        if self.search_hits is not None:
            summary += f"  Search Hits: {self.search_hits}\n"
            summary += f"  Results Returned: {self.result_count}\n"
            summary += f"  Search Time: {self.search_took}ms\n"
        
        if self.error:
            summary += f"  Error: {self.error_type}: {self.error_details}\n"
        
        if self.cache_hit:
            summary += "  Cache: Hit\n"
        elif self.used_fallback:
            summary += "  Fallback: Used database search\n"
        
        logger.info(summary)

class OperationMetricsContextManager:
    """Context manager for operation metrics with proper async protocol implementation"""
    def __init__(self, operation_name: str, metrics_history: List[Dict[str, Any]]):
        self.metrics = OperationMetrics(operation_name)
        self._metrics_history = metrics_history
        self._logger = logging.getLogger(__name__)
        self._start_time: Optional[float] = None
        self._end_time: Optional[float] = None
        self._error: Optional[Exception] = None

    async def __aenter__(self) -> OperationMetrics:
        """Enter the async context manager with proper resource initialization"""
        try:
            self._start_time = time.perf_counter()
            self.metrics.start_time = self._start_time
            
            # Initialize memory tracking
            self.metrics.memory_before = psutil.Process().memory_info().rss
            
            # Log operation start
            self._logger.debug(f"Starting operation: {self.metrics.operation_name}")
            
            return self.metrics
            
        except Exception as e:
            self._logger.error(f"Error in context manager entry: {str(e)}")
            raise

    async def __aexit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[Exception],
        exc_tb: Any
    ) -> None:
        """Exit the async context manager with proper cleanup and error handling"""
        try:
            self._end_time = time.perf_counter()
            self.metrics.end_time = self._end_time
            self.metrics.duration = self._end_time - self._start_time if self._start_time else 0
            
            # Record error if any
            self.metrics.success = exc_type is None
            if exc_val:
                self.metrics.error = exc_val
                self.metrics.error_type = exc_type.__name__ if exc_type else None
                self.metrics.error_details = str(exc_val)
            
            # Final memory measurement
            self.metrics.memory_after = psutil.Process().memory_info().rss
            self.metrics.memory_diff = self.metrics.memory_after - self.metrics.memory_before
            
            # Check if garbage collection is needed
            if self.metrics.memory_diff > 100 * 1024 * 1024:  # 100MB threshold
                self.metrics.gc_triggered = True
                gc.collect()
            
            # Add to metrics history
            try:
                metrics_dict = self.metrics.to_dict()
                self._metrics_history.append(metrics_dict)
            except Exception as e:
                self._logger.error(f"Error recording metrics history: {str(e)}")
            
            # Log completion
            self._log_completion()
            
        except Exception as e:
            self._logger.error(f"Error in context manager exit: {str(e)}")
            if exc_val:
                raise exc_val
            raise

    def _log_completion(self) -> None:
        """Log operation completion with detailed metrics"""
        try:
            log_message = (
                f"\nOperation '{self.metrics.operation_name}' completed:"
                f"\n  Duration: {self.metrics.duration:.3f}s"
                f"\n  Success: {self.metrics.success}"
                f"\n  Memory Impact: {self.metrics.memory_diff / 1024 / 1024:.2f}MB"
            )
            
            if self.metrics.search_hits is not None:
                log_message += (
                    f"\n  Search Hits: {self.metrics.search_hits}"
                    f"\n  Results Returned: {self.metrics.result_count}"
                    f"\n  Search Time: {self.metrics.search_took}ms"
                )
            
            if self.metrics.error:
                log_message += (
                    f"\n  Error: {self.metrics.error_type}: {self.metrics.error_details}"
                )
            
            if self.metrics.cache_hit:
                log_message += "\n  Cache: Hit"
            elif self.metrics.used_fallback:
                log_message += "\n  Fallback: Used database search"
            
            if self.metrics.gc_triggered:
                log_message += "\n  GC: Triggered due to high memory usage"
            
            self._logger.info(log_message)
            
        except Exception as e:
            self._logger.error(f"Error in completion logging: {str(e)}")

class SearchService(SearchServiceProtocol):
    def __init__(self, es_client: AsyncElasticsearch):
        self.es_client = es_client
        self.cache = aiocache.Cache()
        self.query_cache = defaultdict(dict)
        self._reindexing = False
        self._max_result_size = 100
        self._max_batch_size = 50  # Reduced from default
        self._memory_threshold = 70.0  # Percentage
        self._last_gc_time = time.time()
        self._cache_ttl = 300  # 5 minutes
        self._max_cache_size = 1000
        self._cache_cleanup_interval = 600  # 10 minutes
        self._last_cache_cleanup = time.time()
        self._background_tasks = {}
        self._setup_logging()
        self._setup_cache()
        self._connection_pool = {}
        self._metrics_history: List[Dict[str, Any]] = []

    def _setup_logging(self):
        """Configure detailed logging for the search service"""
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler = logging.FileHandler('search_service.log')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

    def _setup_cache(self):
        """Initialize caching system"""
        self.cache = aiocache.Cache(
            cache_class=aiocache.SimpleMemoryCache,
            namespace="search_service",
            timeout=300  # 5 minutes default timeout
        )
        self.query_cache = defaultdict(lambda: {"timestamp": 0, "result": None})

    async def _check_memory_usage(self):
        """Monitor and manage memory usage"""
        current_time = time.time()
        
        # Regular memory check
        if current_time - self._last_gc_time > 300:  # Check every 5 minutes
            memory_percent = psutil.Process().memory_percent()
            logger.debug(f"Current memory usage: {memory_percent}%")
            
            if memory_percent > self._memory_threshold:
                logger.warning(f"High memory usage detected: {memory_percent}%")
                gc.collect()
                self._last_gc_time = current_time
                
                # Clear caches if memory is still high
                if psutil.Process().memory_percent() > self._memory_threshold:
                    logger.warning("Clearing caches due to high memory usage")
                    await self.cache.clear()
                    self.query_cache.clear()
        
        # Regular cache cleanup
        if current_time - self._last_cache_cleanup > self._cache_cleanup_interval:
            logger.info("Performing regular cache cleanup")
            await self._cleanup_cache()
            self._last_cache_cleanup = current_time
            
    async def _cleanup_cache(self):
        """Clean up expired cache entries"""
        current_time = time.time()
        # Clean query cache
        expired_keys = []
        for key, data in self.query_cache.items():
            if current_time - data.get("timestamp", 0) > self._cache_ttl:
                expired_keys.append(key)
        
        for key in expired_keys:
            del self.query_cache[key]
            
        # Trim cache if too large
        if len(self.query_cache) > self._max_cache_size:
            sorted_items = sorted(
                self.query_cache.items(),
                key=lambda x: x[1].get("timestamp", 0)
            )
            for key, _ in sorted_items[:-self._max_cache_size]:
                del self.query_cache[key]
                
        # Clean aiocache
        await self.cache.clear()

    @lru_cache(maxsize=1000)
    def _build_cache_key(self, **kwargs) -> str:
        """Build a cache key from search parameters"""
        return json.dumps(sorted(kwargs.items()))

    async def _get_cached_result(self, cache_key: str) -> Optional[Dict]:
        """Get cached search result if valid"""
        cached = self.query_cache[cache_key]
        if cached["timestamp"] > time.time() - 60:  # 1-minute cache
            return cached["result"]
        return None

    async def _cache_result(self, cache_key: str, result: Dict):
        """Cache search result"""
        self.query_cache[cache_key] = {
            "timestamp": time.time(),
            "result": result
        }

    async def _optimize_query(self, stmt) -> Any:
        """Optimize SQL query based on execution plan"""
        try:
            async with AsyncSessionLocal() as session:
                # Get query execution plan
                plan = await session.execute(
                    text(f"EXPLAIN QUERY PLAN {stmt.compile(compile_kwargs={'literal_binds': True})}")
                )
                plan_lines = plan.fetchall()
                
                # Log the execution plan
                logger.debug(f"Query execution plan: {plan_lines}")
                
                # Add index hints if needed
                if any("SCAN TABLE" in str(line) for line in plan_lines):
                    logger.warning("Full table scan detected, adding index hints")
                    stmt = stmt.with_hint(Email, 'USE INDEX (ix_emails_date)')
                
                return stmt
        except Exception as e:
            logger.error(f"Error optimizing query: {str(e)}")
            return stmt

    async def _get_connection(self, account: str) -> AsyncElasticsearch:
        """Get or create Elasticsearch connection from pool"""
        if account not in self._connection_pool:
            self._connection_pool[account] = AsyncElasticsearch(
                hosts=[settings.ELASTICSEARCH_HOST],
                retry_on_timeout=True,
                max_retries=3,
                timeout=30,
                sniff_on_start=True,
                sniff_on_connection_fail=True,
                sniffer_timeout=60
            )
        return self._connection_pool[account]

    async def _background_reindex(self, db: AsyncSession):
        """Background task to reindex emails from SQLite to Elasticsearch"""
        async with self._measure_operation_time("Background reindexing") as metrics:
            try:
                if self._reindexing:
                    logger.warning("Reindexing already in progress, skipping")
                    return

                self._reindexing = True
                logger.info("Starting background reindexing")
                
                # Get total count first
                count_stmt = select(func.count()).select_from(Email)
                total_count = await db.scalar(count_stmt) or 0
                logger.info(f"Total emails to reindex: {total_count}")
                
                # Get all emails from SQLite with optimized query
                stmt = select(Email).order_by(Email.id)  # Order by ID for consistent batching
                result = await db.execute(stmt)
                emails = result.scalars().all()
                
                # Batch process emails with progress tracking
                batch_size = 100
                processed = 0
                failed = 0
                
                for i in range(0, len(emails), batch_size):
                    batch = emails[i:i + batch_size]
                    actions = []
                    
                    for email in batch:
                        try:
                            actions.append({
                                "_index": "emails",
                                "_id": str(email.id),
                                "_source": email.to_elastic_doc()
                            })
                        except Exception as e:
                            logger.error(f"Error preparing email {email.id} for indexing: {str(e)}")
                            failed += 1
                            continue
                    
                    if actions:
                        try:
                            # Add retry logic for bulk indexing
                            max_retries = 3
                            retry_count = 0
                            while retry_count < max_retries:
                                try:
                                    response = await self.es_client.bulk(body=actions, refresh=True)
                                    if response.get("errors", False):
                                        # Log specific bulk operation errors
                                        for item in response["items"]:
                                            if "error" in item["index"]:
                                                logger.error(f"Bulk indexing error: {item['index']['error']}")
                                                failed += 1
                                    processed += len(actions)
                                    break
                                except ConnectionError as e:
                                    retry_count += 1
                                    if retry_count == max_retries:
                                        raise
                                    logger.warning(f"Bulk indexing attempt {retry_count} failed, retrying: {str(e)}")
                                    await asyncio.sleep(1)
                            
                            progress = (processed / total_count) * 100
                            logger.info(f"Indexed batch of {len(actions)} emails. Progress: {progress:.1f}% ({processed}/{total_count})")
                            
                        except Exception as e:
                            logger.error(f"Error during bulk indexing: {str(e)}")
                            failed += 1
                    
                    # Adaptive sleep based on system load
                    await asyncio.sleep(0.1)
                
                logger.info(f"Background reindexing completed. Processed: {processed}, Failed: {failed}")
                
            except SQLAlchemyError as e:
                logger.error(f"Database error during reindexing: {str(e)}")
                raise IndexingError(f"Database error: {str(e)}")
            except Exception as e:
                logger.error(f"Error during background reindexing: {str(e)}")
                raise IndexingError(f"Indexing error: {str(e)}")
            finally:
                self._reindexing = False

    async def ensure_index_exists(self, db: AsyncSession) -> bool:
        """Ensure the email index exists and start reindexing if needed"""
        try:
            exists = await self.es_client.indices.exists(index="emails")
            if not exists:
                logger.info("Creating email index")
                await self.es_client.indices.create(
                    index="emails",
                    body={
                        "mappings": {
                            "properties": {
                                "account": {"type": "keyword"},
                                "subject": {"type": "text"},
                                "sender": {"type": "keyword"},
                                "recipient": {"type": "keyword"},
                                "date": {"type": "date"},
                                "category": {"type": "keyword"},
                                "content": {"type": "text"},
                                "has_attachments": {"type": "boolean"}
                            }
                        }
                    }
                )
                
                # Start background reindexing
                if not self._reindexing:
                    asyncio.create_task(self._background_reindex(db))
                return False
            
            # Check if index is empty
            count = await self.es_client.count(index="emails")
            if count["count"] == 0 and not self._reindexing:
                # Start background reindexing
                asyncio.create_task(self._background_reindex(db))
                return False
                
            return True
        except Exception as e:
            logger.error(f"Error ensuring index exists: {e}")
            return False

    @asynccontextmanager
    async def _measure_operation_time(self, operation_name: str) -> AsyncIterator[OperationMetrics]:
        """
        Async context manager for measuring operation time and resource usage.
        
        Args:
            operation_name: Name of the operation being measured
            
        Yields:
            OperationMetrics: Metrics object for tracking operation performance
            
        Example:
            async with self._measure_operation_time("search_operation") as metrics:
                # Perform operation
                result = await self.perform_search()
                metrics.search_hits = len(result)
        """
        context_manager = OperationMetricsContextManager(operation_name, self._metrics_history)
        async with context_manager as metrics:
            yield metrics

    async def search_emails(self, db, query=None, category=None, sender=None, account=None, size=25, offset=0):
        """Search emails with memory-efficient pagination."""
        try:
            # Validate and limit size
            size = min(size, self._max_result_size)
            
            # Build search query
            search_query = {
                "query": {
                    "bool": {
                        "must": []
                    }
                },
                "sort": [{"date": "desc"}],
                "size": size,
                "from": offset
            }
            
            if query:
                search_query["query"]["bool"]["must"].append({
                    "multi_match": {
                        "query": query,
                        "fields": ["subject^2", "content", "sender"],
                        "type": "best_fields"
                    }
                })
            
            if category:
                search_query["query"]["bool"]["must"].append({
                    "term": {"category.keyword": category}
                })
            
            if sender:
                search_query["query"]["bool"]["must"].append({
                    "term": {"sender.keyword": sender}
                })
            
            if account:
                search_query["query"]["bool"]["must"].append({
                    "term": {"account.keyword": account}
                })
            
            # Execute search with scroll for large results
            if size > 100:
                results = await self._scroll_search(search_query, size)
            else:
                results = await self.es_client.search(
                    index="emails",
                    body=search_query
                )
            
            # Process results
            hits = results.get("hits", {}).get("hits", [])
            total = results.get("hits", {}).get("total", {}).get("value", 0)
            
            emails = []
            for hit in hits:
                source = hit["_source"]
                emails.append({
                    "id": source.get("id"),
                    "message_id": source.get("message_id"),
                    "subject": source.get("subject"),
                    "sender": source.get("sender"),
                    "date": source.get("date"),
                    "category": source.get("category", "Uncategorized"),
                    "is_read": source.get("is_read", False),
                    "account": source.get("account")
                })
            
            return {
                "total": total,
                "emails": emails,
                "has_more": total > (offset + size)
            }
            
        except Exception as e:
            logger.error(f"Error searching emails: {str(e)}")
            raise

    async def _scroll_search(self, search_query, total_size):
        """Helper method to handle scrolling for large result sets."""
        try:
            # Initialize scroll
            search_query["size"] = min(100, total_size)  # Process in chunks of 100
            results = await self.es_client.search(
                index="emails",
                body=search_query,
                scroll="2m"  # Keep scroll context alive for 2 minutes
            )
            
            scroll_id = results["_scroll_id"]
            hits = results["hits"]["hits"]
            total_hits = len(hits)
            
            # Continue scrolling if we need more results
            while total_hits < total_size:
                scroll_results = await self.es_client.scroll(
                    scroll_id=scroll_id,
                    scroll="2m"
                )
                
                # Break if no more results
                if not scroll_results["hits"]["hits"]:
                    break
                
                hits.extend(scroll_results["hits"]["hits"])
                total_hits = len(hits)
                scroll_id = scroll_results["_scroll_id"]
            
            # Clean up scroll context
            await self.es_client.clear_scroll(
                body={"scroll_id": scroll_id}
            )
            
            # Return results in same format as regular search
            return {
                "hits": {
                    "total": {"value": total_hits},
                    "hits": hits[:total_size]
                }
            }
            
        except Exception as e:
            logger.error(f"Error during scroll search: {str(e)}")
            raise

    async def _reindex_from_sqlite(self):
        """Helper method to reindex all emails from SQLite to Elasticsearch."""
        from ..core.database import AsyncSessionLocal
        from sqlalchemy import select, func
        from ..models.email import Email
        
        logger.info("\n=== Starting reindex from SQLite ===")
        try:
            async with AsyncSessionLocal() as session:
                # Get total count
                count_query = select(func.count()).select_from(Email)
                total_count = await session.scalar(count_query)
                
                logger.info(f"Found {total_count} emails in SQLite")
                
                # Process in batches
                offset = 0
                batch_size = self._max_batch_size
                
                while offset < total_count:
                    query = select(Email).order_by(Email.id).offset(offset).limit(batch_size)
                    result = await session.execute(query)
                    batch = result.scalars().all()
                    
                    if not batch:
                        break
                    
                    logger.info(f"Processing batch of {len(batch)} emails (offset: {offset})")
                    
                    for email in batch:
                        try:
                            await self.index_email(email)
                        except Exception as e:
                            logger.error(f"Error indexing email {email.message_id}: {str(e)}")
                            continue
                    
                    offset += len(batch)
                    # Add small delay between batches
                    await asyncio.sleep(0.1)
                
                logger.info("=== Reindex from SQLite completed ===\n")
        except Exception as e:
            logger.error(f"Error during reindex: {str(e)}")
            raise

    async def index_email(self, email: Email):
        """Index a single email document."""
        try:
            doc = {
                "message_id": email.message_id,
                "subject": email.subject,
                "sender": email.sender,
                "date": email.date.isoformat() if email.date else None,
                "category": email.category,
                "content": email.content,
                "is_read": email.is_read,
                "account": email.account,
                "created_at": email.created_at.isoformat() if email.created_at else None,
                "updated_at": email.updated_at.isoformat() if email.updated_at else None
            }
            
            logger.info("\n=== Indexing email in Elasticsearch ===")
            logger.info(f"Document to index: {doc}")
            
            # Verify the index exists
            exists = await self.es_client.indices.exists(index="emails")
            logger.info(f"Index emails exists: {exists}")
            
            if not exists:
                logger.info("Index emails doesn't exist, creating...")
                await self.init_index()
            
            # Index the document
            response = await self.es_client.index(
                index="emails",
                id=email.message_id,
                document=doc,
                refresh=True  # Make document immediately searchable
            )
            
            logger.info(f"Indexing response: {response}")
            
            # Verify the document was indexed
            exists = await self.es_client.exists(
                index="emails",
                id=email.message_id
            )
            logger.info(f"Document exists after indexing: {exists}")
            
            # Get document count in index
            stats = await self.es_client.indices.stats(index="emails")
            doc_count = stats['_all']['primaries']['docs']['count']
            logger.info(f"Total documents in index: {doc_count}")
            
            logger.info("=== Email indexing completed successfully ===\n")
            
        except Exception as e:
            logger.error(f"Error indexing email: {str(e)}")
            logger.error(f"Error type: {type(e)}")
            logger.error(f"Error details: {getattr(e, 'info', {})}")
            raise

    async def init_index(self):
        """Initialize the email index with proper mappings."""
        # First ensure Elasticsearch is available
        await self.wait_for_elasticsearch()
        
        max_retries = 5
        retry_delay = 5  # seconds
        
        for attempt in range(max_retries):
            try:
                # Check if index exists
                exists = await self.es_client.indices.exists(index="emails")
                if exists:
                    logger.info("Index emails already exists, deleting...")
                    await self.es_client.indices.delete(index="emails")
                    logger.info("Successfully deleted index emails")
                
                mapping = {
                    "mappings": {
                        "properties": {
                            "message_id": {"type": "keyword"},
                            "subject": {
                                "type": "text",
                                "fields": {
                                    "keyword": {"type": "keyword"}
                                }
                            },
                            "sender": {
                                "type": "text",
                                "fields": {
                                    "keyword": {"type": "keyword"}
                                }
                            },
                            "date": {
                                "type": "date",
                                "format": "strict_date_optional_time||epoch_millis||yyyy-MM-dd'T'HH:mm:ss||yyyy-MM-dd'T'HH:mm:ssZ"
                            },
                            "category": {"type": "keyword"},
                            "content": {"type": "text"},
                            "is_read": {"type": "boolean"},
                            "account": {"type": "keyword"},
                            "created_at": {
                                "type": "date",
                                "format": "strict_date_optional_time||epoch_millis"
                            },
                            "updated_at": {
                                "type": "date",
                                "format": "strict_date_optional_time||epoch_millis"
                            }
                        }
                    },
                    "settings": {
                        "index": {
                            "number_of_shards": 1,
                            "number_of_replicas": 0,
                            "refresh_interval": "1s"
                        }
                    }
                }
                
                logger.info(f"Creating new index emails with mappings: {json.dumps(mapping, indent=2)}")
                await self.es_client.indices.create(index="emails", body=mapping)
                logger.info("Successfully created index emails")
                
                # Verify the index was created
                stats = await self.es_client.indices.stats(index="emails")
                stats_dict = stats.body if hasattr(stats, 'body') else stats
                logger.info(f"Index stats after creation: {json.dumps(stats_dict, indent=2)}")
                
                # Get the mapping to verify it was set correctly
                mapping_result = await self.es_client.indices.get_mapping(index="emails")
                mapping_dict = mapping_result.body if hasattr(mapping_result, 'body') else mapping_result
                logger.info(f"Index mapping after creation: {json.dumps(mapping_dict, indent=2)}")
                
                return
            except Exception as e:
                logger.error(f"Error in attempt {attempt + 1}: {str(e)}")
                if hasattr(e, 'info'):
                    logger.error(f"Error info: {e.info}")
                await asyncio.sleep(retry_delay)
        
        logger.error("Failed to initialize index after multiple attempts")
        raise

    async def wait_for_elasticsearch(self):
        """Wait for Elasticsearch to become available."""
        max_retries = 5
        retry_delay = 5  # seconds
        
        for attempt in range(max_retries):
            try:
                await self.es_client.info()
                logger.info("Successfully connected to Elasticsearch")
                return True
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
                if attempt < max_retries - 1:
                    logger.info(f"Waiting {retry_delay} seconds before next attempt...")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error("Failed to connect to Elasticsearch after all attempts")
                    raise

    async def delete_email(self, message_id: str):
        """Delete a single email document from the search index."""
        try:
            await self.es_client.delete(
                index="emails",
                id=message_id,
                refresh=True  # Make deletion immediately visible
            )
            logger.info(f"Successfully deleted email {message_id} from search index")
        except Exception as e:
            logger.error(f"Error deleting email from search index: {str(e)}")
            raise

    async def close(self):
        """Close the Elasticsearch connection."""
        await self.es_client.close()

    async def clear_index(self):
        """Clear all data from the search index."""
        try:
            # Check if index exists
            exists = await self.es_client.indices.exists(index="emails")
            if exists:
                logger.info(f"Deleting index emails...")
                await self.es_client.indices.delete(index="emails")
                logger.info(f"Successfully deleted index emails")
            
            # Recreate the index with proper mappings
            await self.init_index()
            logger.info("Successfully recreated empty index with proper mappings")
            
        except Exception as e:
            logger.error(f"Error clearing search index: {str(e)}")
            raise

    async def cleanup(self):
        """Cleanup resources"""
        try:
            # Close all connections in pool
            for client in self._connection_pool.values():
                await client.close()
            self._connection_pool.clear()
            
            # Clear caches
            await self.cache.clear()
            self.query_cache.clear()
            
            # Force garbage collection
            gc.collect()
            
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}") 
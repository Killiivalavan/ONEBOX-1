from typing import Dict, Optional
from elasticsearch import AsyncElasticsearch
from .interfaces import (
    ServiceFactoryProtocol,
    SearchServiceProtocol,
    EmailServiceProtocol,
    NotificationServiceProtocol
)
from .config import settings
import logging

logger = logging.getLogger(__name__)

class ServiceFactory(ServiceFactoryProtocol):
    """Service factory for managing application services"""
    
    _instance = None
    
    def __init__(self):
        self._es_client: Optional[AsyncElasticsearch] = None
        self._search_service: Optional[SearchServiceProtocol] = None
        self._email_service: Optional[EmailServiceProtocol] = None
        self._notification_service: Optional[NotificationServiceProtocol] = None
        self._initialized = False
    
    @classmethod
    def get_instance(cls) -> 'ServiceFactory':
        if cls._instance is None:
            cls._instance = ServiceFactory()
        return cls._instance
    
    def initialize(self):
        """Initialize all services in the correct order"""
        if self._initialized:
            logger.warning("Services already initialized")
            return

        try:
            # Initialize Elasticsearch client
            self._es_client = AsyncElasticsearch(
                hosts=[settings.ELASTICSEARCH_HOST],
                retry_on_timeout=True,
                max_retries=3,
                request_timeout=30
            )
            
            # Import services here to avoid circular imports
            from ..services.search_service import SearchService
            from ..services.email_service import EmailService
            from ..services.notification_service import NotificationService
            
            # Initialize services in dependency order
            self._search_service = SearchService(self._es_client)
            self._notification_service = NotificationService()
            self._email_service = EmailService(
                search_service=self._search_service,
                notification_service=self._notification_service
            )
            
            self._initialized = True
            logger.info("Services initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize services: {str(e)}")
            if hasattr(e, '__cause__'):
                logger.error(f"Caused by: {e.__cause__}")
            raise
    
    async def cleanup(self):
        """Cleanup services during shutdown"""
        try:
            if self._es_client:
                await self._es_client.close()
            if self._email_service and hasattr(self._email_service, 'close'):
                await self._email_service.close()
            self._initialized = False
            logger.info("Services cleaned up successfully")
        except Exception as e:
            logger.error(f"Error during service cleanup: {str(e)}")
            raise
    
    @property
    def search_service(self) -> SearchServiceProtocol:
        if not self._initialized:
            raise RuntimeError("Services not initialized. Call initialize() first")
        return self._search_service
    
    @property
    def email_service(self) -> EmailServiceProtocol:
        if not self._initialized:
            raise RuntimeError("Services not initialized. Call initialize() first")
        return self._email_service
    
    @property
    def notification_service(self) -> NotificationServiceProtocol:
        if not self._initialized:
            raise RuntimeError("Services not initialized. Call initialize() first")
        return self._notification_service
    
    @property
    def es_client(self) -> AsyncElasticsearch:
        if not self._initialized:
            raise RuntimeError("Services not initialized. Call initialize() first")
        return self._es_client

# Create global instance
service_factory = ServiceFactory.get_instance() 
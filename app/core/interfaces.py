from typing import Protocol, Dict, Any, Optional, List
from datetime import datetime
from elasticsearch import AsyncElasticsearch
from sqlalchemy.orm import Session

class SearchServiceProtocol(Protocol):
    """Protocol defining the search service interface"""
    async def search_emails(
        self,
        db: Session,
        query: Optional[str] = None,
        category: Optional[str] = None,
        sender: Optional[str] = None,
        account: Optional[str] = None,
        size: int = 25,
        offset: int = 0
    ) -> Dict[str, Any]:
        ...

    async def ensure_index_exists(self, db: Session) -> bool:
        ...

    async def delete_email(self, message_id: str) -> None:
        ...

    async def clear_index(self) -> None:
        ...

    async def wait_for_elasticsearch(self) -> bool:
        ...

class EmailServiceProtocol(Protocol):
    """Protocol defining the email service interface"""
    async def get_recent_emails(self, offset: int = 0, limit: int = 20) -> Dict[str, Any]:
        ...

    async def add_account(self, account: Dict[str, str]) -> Dict[str, str]:
        ...

    async def get_email_content(self, message_id: str) -> str:
        ...

    async def test_connection(self) -> Dict[str, Any]:
        ...

    async def get_task_status(self, task_id: str) -> Dict[str, Any]:
        ...

class NotificationServiceProtocol(Protocol):
    """Protocol defining the notification service interface"""
    async def send_notification(
        self,
        subject: str,
        sender: str,
        category: str,
        importance: str,
        date: datetime
    ) -> Dict[str, Any]:
        ...

    async def send_error_notification(
        self,
        error_message: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        ...

class ServiceFactoryProtocol(Protocol):
    """Protocol defining the service factory interface"""
    @property
    def search_service(self) -> SearchServiceProtocol:
        ...

    @property
    def email_service(self) -> EmailServiceProtocol:
        ...

    @property
    def es_client(self) -> AsyncElasticsearch:
        ...

    def initialize(self) -> None:
        ...

    async def cleanup(self) -> None:
        ... 
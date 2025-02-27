from pydantic import BaseModel
from typing import List, Dict, Optional, Any, Literal

class SearchResponse(BaseModel):
    emails: List[Dict]
    total: int
    aggregations: Dict

class EmailResponse(BaseModel):
    emails: List[Dict]
    total: int
    hasMore: bool

class Account(BaseModel):
    email: str
    name: Optional[str] = ""
    type: Optional[str] = "gmail"  # Default to gmail for backward compatibility
    categories: Optional[List[str]] = []

class NewAccount(BaseModel):
    email: str
    password: str
    type: str = "gmail"  # Default to gmail for backward compatibility

class ReplyResponse(BaseModel):
    reply: str

class ClassificationResponse(BaseModel):
    category: str
    confidence: float

class EmailText(BaseModel):
    text: str

class ReplyRequest(BaseModel):
    email_text: str
    context: Optional[Dict[str, str]] = None

class TaskResponse(BaseModel):
    task_id: str
    status: str
    message: str

class TaskStatus(BaseModel):
    status: Literal["pending", "completed", "failed"]
    progress: int
    message: str
    error: Optional[str] = None
    result: Optional[Any] = None 
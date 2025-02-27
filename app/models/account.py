from pydantic import BaseModel, EmailStr
from typing import Optional, Dict, Any

class AccountBase(BaseModel):
    email: EmailStr
    type: str
    imap_server: str
    imap_port: int

class AccountCreate(AccountBase):
    password: str

class Account(AccountBase):
    id: int
    status: str
    
    class Config:
        from_attributes = True

class AccountResponse(BaseModel):
    email: str
    type: str
    status: str

class TaskResponse(BaseModel):
    task_id: str
    status: str
    message: str
    progress: Optional[int] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None 
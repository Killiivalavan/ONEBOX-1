from fastapi import APIRouter, HTTPException, Depends
from typing import List, Dict, Any, Optional
from ....core.service_factory import service_factory
from ....models.account import AccountCreate, Account, AccountResponse, TaskResponse
from fastapi.security import OAuth2PasswordBearer
from ....core.config import settings

router = APIRouter()

# Simple auth scheme - can be enhanced based on your needs
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

async def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    """Simple user authentication - enhance as needed"""
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated"
        )
    return token

@router.post("/add")
async def add_account(account_data: Dict[str, str]) -> Dict[str, Any]:
    """Add a new email account"""
    try:
        result = await service_factory.email_service.add_account({
            "email": account_data.get("email"),
            "password": account_data.get("password"),
            "type": account_data.get("type", "gmail")
        })
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/task/{task_id}")
async def get_task_status(task_id: str) -> Dict[str, Any]:
    """Get the status of an account addition task"""
    try:
        return await service_factory.email_service.get_task_status(task_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ... rest of the existing endpoints ... 
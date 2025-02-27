from fastapi import FastAPI, HTTPException, Form, Depends, Query, Body, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Union, List, Optional, Dict
from datetime import datetime
import json
from .services.ai_service import ai_service
from .core.service_factory import service_factory
from .core.database import init_db, get_db, AsyncSessionLocal
from .core.config import settings
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, text
from .models.email import Email
from .models.response import (
    SearchResponse,
    Account,
    NewAccount,
    EmailResponse,
    ReplyResponse,
    ClassificationResponse,
    EmailText,
    ReplyRequest,
    TaskResponse,
    TaskStatus
)
import logging
import sys
import asyncio
from elasticsearch import AsyncElasticsearch
import time
import os
from pydantic import ValidationError

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('app.log', mode='a', encoding='utf-8')
    ]
)

# Set log levels for specific modules
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
logging.getLogger('aioimaplib').setLevel(logging.DEBUG)
logging.getLogger('email_service').setLevel(logging.DEBUG)
logging.getLogger('uvicorn').setLevel(logging.INFO)
logging.getLogger('fastapi').setLevel(logging.DEBUG)

# Create logger for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Initialize services
es_client = AsyncElasticsearch(
    hosts=[settings.ELASTICSEARCH_HOST],
    retry_on_timeout=True,
    max_retries=3,
    request_timeout=30
)

# Create FastAPI app
app = FastAPI(
    title="ReachInbox API",
    description="API for email management with AI-powered features",
    version="1.0.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8001"],  # Fixed typo in frontend origin
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],  # Explicitly specify allowed methods
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600  # Cache preflight requests for 1 hour
)

# Add request logging middleware with timeout
@app.middleware("http")
async def log_requests(request, call_next):
    start_time = time.time()
    logger.info(f"Request started: {request.method} {request.url}")
    try:
        # Add timeout to request handling
        response = await asyncio.wait_for(
            call_next(request),
            timeout=5.0  # 5 second timeout for all requests
        )
        duration = time.time() - start_time
        logger.info(f"Request completed: {request.method} {request.url} - Status: {response.status_code} - Duration: {duration:.2f}s")
        return response
    except asyncio.TimeoutError:
        logger.error(f"Request timed out: {request.method} {request.url}")
        raise HTTPException(
            status_code=504,
            detail="Request timed out"
        )
    except ValidationError as ve:
        logger.error(f"Validation error: {request.method} {request.url} - Error: {str(ve)}")
        raise HTTPException(
            status_code=422,
            detail=str(ve)
        )
    except Exception as e:
        logger.error(f"Request failed: {request.method} {request.url} - Error: {str(e)}")
        if hasattr(e, '__cause__'):
            logger.error(f"Caused by: {e.__cause__}")
        # Include error type in log
        logger.error(f"Error type: {type(e).__name__}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

# Initialize services
@app.on_event("startup")
async def startup_event():
    """Initialize application services on startup."""
    try:
        print("Starting application initialization...")  # Print directly to ensure we see it
        logger.info("Starting application initialization...")
        
        # Check if we should skip services based on debug settings
        skip_search = os.getenv('DISABLE_SEARCH', 'true').lower() == 'true'  # Default to true in debug
        skip_email = os.getenv('DISABLE_EMAIL_SERVICE', 'true').lower() == 'true'  # Default to true in debug
        
        logger.info(f"Service flags: DISABLE_SEARCH={skip_search}, DISABLE_EMAIL_SERVICE={skip_email}")
        
        # Initialize database with timeout
        print("Initializing database...")  # Print directly to ensure we see it
        logger.info("Initializing database...")
        try:
            await asyncio.wait_for(init_db(), timeout=2.0)  # Reduced timeout
            logger.info("Database initialization completed")
        except asyncio.TimeoutError:
            logger.error("Database initialization timed out")
            # Don't raise, just log the error
            return
        except Exception as e:
            logger.error(f"Database initialization failed: {str(e)}")
            # Don't raise, just log the error
            return
        
        if not skip_email:
            # Initialize services with timeout
            print("Initializing services...")  # Print directly to ensure we see it
            logger.info("Initializing services...")
            try:
                # Initialize services synchronously to avoid timeout issues
                service_factory.initialize()
                logger.info("Services initialization completed")
            except Exception as e:
                logger.error(f"Services initialization failed: {str(e)}")
                # Don't raise, continue with reduced functionality
        else:
            logger.info("Skipping email service initialization (disabled in debug mode)")
        
        if not skip_search:
            # Wait for Elasticsearch with reduced timeout
            print("Waiting for Elasticsearch...")  # Print directly to ensure we see it
            logger.info("Waiting for Elasticsearch...")
            try:
                await asyncio.wait_for(
                    service_factory.search_service.wait_for_elasticsearch(),
                    timeout=2.0  # Reduced timeout
                )
                logger.info("Elasticsearch connection established")
            except (asyncio.TimeoutError, Exception) as e:
                logger.error(f"Elasticsearch initialization failed: {str(e)}")
                # Don't raise, continue with reduced functionality
            
            # Ensure index exists with timeout
            print("Ensuring index exists...")  # Print directly to ensure we see it
            logger.info("Ensuring index exists...")
            try:
                async with AsyncSessionLocal() as session:
                    await asyncio.wait_for(
                        service_factory.search_service.ensure_index_exists(session),
                        timeout=2.0  # Reduced timeout
                    )
                logger.info("Index check completed")
            except (asyncio.TimeoutError, Exception) as e:
                logger.error(f"Index check failed: {str(e)}")
                # Don't raise, continue with reduced functionality
        else:
            logger.info("Skipping Elasticsearch initialization (disabled in debug mode)")
        
        print("Application startup completed successfully")  # Print directly to ensure we see it
        logger.info("Application startup completed successfully")
    except Exception as e:
        print(f"Failed to initialize services: {str(e)}")  # Print directly to ensure we see it
        logger.error(f"Failed to initialize services: {str(e)}")
        if hasattr(e, '__cause__'):
            logger.error(f"Caused by: {e.__cause__}")
        # Don't raise, let the application start with reduced functionality

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown."""
    await es_client.close()
    await service_factory.cleanup()

@app.get("/")
async def root():
    """
    Root endpoint to verify API is running.
    """
    return {"message": "Welcome to ReachInbox API"}

@app.get("/health")
async def health_check():
    """
    Health check endpoint to verify service status.
    """
    try:
        # Add timeout to database check
        async with AsyncSessionLocal() as session:
            try:
                await asyncio.wait_for(
                    session.execute(text("SELECT 1")),
                    timeout=2.0
                )
                logger.info("Database health check passed")
            except asyncio.TimeoutError:
                logger.error("Database health check timed out")
                raise HTTPException(
                    status_code=503,
                    detail="Database health check timed out"
                )
            except Exception as e:
                logger.error(f"Database health check failed: {str(e)}")
                raise HTTPException(
                    status_code=503,
                    detail=f"Database health check failed: {str(e)}"
                )

        # Check if services are disabled in debug mode
        skip_search = os.getenv('DISABLE_SEARCH', 'false').lower() == 'true'
        skip_email = os.getenv('DISABLE_EMAIL_SERVICE', 'false').lower() == 'true'

        status = {
            "status": "healthy",
            "database": "connected",
            "search_service": "disabled" if skip_search else "enabled",
            "email_service": "disabled" if skip_email else "enabled",
            "timestamp": datetime.now().isoformat()
        }

        return status

    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        raise HTTPException(
            status_code=503,
            detail=f"Service unhealthy: {str(e)}"
        )

@app.post("/test/classify", response_model=ClassificationResponse)
async def test_classification(text: Union[str, EmailText]):
    """
    Test endpoint for Groq AI classification.
    """
    try:
        # Handle both string and JSON inputs
        email_text = text if isinstance(text, str) else text.text
        category = await ai_service.classify_email(email_text)
        return ClassificationResponse(category=category, confidence=0.95)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Alternative endpoint that accepts form data
@app.post("/test/classify/form", response_model=ClassificationResponse)
async def test_classification_form(text: str = Form(...)):
    """
    Test endpoint for Qroq AI classification using form data.
    
    Parameters:
    - text: The email text to classify (as form data)
    
    Returns:
    - category: The classified category
    - confidence: Confidence score of the classification
    """
    try:
        category = await ai_service.classify_email(text)
        return ClassificationResponse(category=category, confidence=0.95)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/test/reply", response_model=ReplyResponse)
async def test_reply_generation(request: ReplyRequest):
    """
    Test endpoint for Groq AI reply generation.
    """
    try:
        reply = await ai_service.generate_reply(request.email_text, request.context)
        return ReplyResponse(reply=reply)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/email/test-connection")
async def test_email_connection():
    """
    Test endpoint to verify email connection.
    """
    try:
        # Get the first account from settings
        result = await service_factory.email_service.test_connection()
        return {"status": "success", "message": "Email connection successful", "details": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/email/recent", response_model=EmailResponse)
async def get_recent_emails(
    offset: int = Query(0, description="Number of emails to skip"),
    limit: int = Query(20, description="Number of emails to return")
):
    """
    Get recent emails from the inbox with pagination support.
    """
    try:
        # First, fetch new emails from all accounts
        print("\n=== Fetching new emails before getting recent ===")
        async with AsyncSessionLocal() as session:
            # Get existing emails
            query = select(Email)
            result = await session.execute(query)
            existing_emails = {email.message_id: email for email in result.scalars().all()}
            
            # Fetch new emails
            await service_factory.email_service._fetch_and_process_new_emails(existing_emails)
            print("=== Email fetch completed ===\n")
        
        return await service_factory.email_service.get_recent_emails(offset=offset, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/email/updates")
async def get_email_updates(last_update: Optional[str] = Query(None)):
    """
    Get new emails since the last update.
    This endpoint is meant to be polled periodically for real-time updates.
    """
    try:
        # Convert last_update to datetime if provided
        last_update_dt = datetime.fromisoformat(last_update) if last_update else None
        
        async with AsyncSessionLocal() as session:
            query = select(Email)
            if last_update_dt:
                query = query.where(Email.created_at > last_update_dt)
            query = query.order_by(Email.date.desc())
            
            result = await session.execute(query)
            new_emails = result.scalars().all()
            
            return {
                "emails": [email.to_dict() for email in new_emails],
                "lastUpdate": datetime.now().isoformat()
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/email/search", response_model=SearchResponse)
async def search_emails(
    query: str = None,
    category: str = None,
    sender: str = None,
    account: str = None,
    size: int = 25,
    offset: int = 0,
    background_tasks: BackgroundTasks = None,
    db = Depends(get_db)
):
    """Search emails with immediate response and background indexing"""
    try:
        # Start background task to ensure index exists and is populated
        if background_tasks:
            background_tasks.add_task(
                service_factory.search_service.ensure_index_exists,
                db
            )
        
        # Perform search
        result = await service_factory.search_service.search_emails(
            db=db,
            query=query,
            category=category,
            sender=sender,
            account=account,
            size=size,
            offset=offset
        )
        
        return result
    except Exception as e:
        logger.error(f"Error searching emails: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/test/notification")
async def test_notification():
    """
    Test endpoint for Slack notifications.
    Sends a test notification to verify the Slack webhook integration.
    """
    try:
        # Send a test email notification
        email_result = await service_factory.notification_service.send_notification(
            subject="Test Email Subject",
            sender="test@example.com",
            category="Test",
            importance="normal",
            date=datetime.now()
        )
        
        # Send a test error notification
        error_result = await service_factory.notification_service.send_error_notification(
            "Test Error Message",
            {"context": "Testing error notifications"}
        )
        
        return {
            "status": "success",
            "email_notification": email_result,
            "error_notification": error_result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/accounts", response_model=List[Account])
async def get_accounts():
    """Get configured email accounts"""
    try:
        logger.info("Starting accounts fetch")
        start_time = time.time()

        # Check if email service is disabled
        email_service_disabled = os.getenv('DISABLE_EMAIL_SERVICE', 'false').lower() == 'true'
        logger.info(f"Email service disabled: {email_service_disabled}")

        if email_service_disabled:
            logger.warning("Email service is disabled, returning empty account list")
            return []

        # First check if settings is accessible
        if not hasattr(settings, 'EMAIL_ACCOUNTS'):
            logger.error("EMAIL_ACCOUNTS not found in settings")
            raise HTTPException(
                status_code=500,
                detail="Email accounts configuration is missing"
            )

        # Log current settings state
        logger.info(f"Current EMAIL_ACCOUNTS setting: {settings.EMAIL_ACCOUNTS}")

        # Transform accounts with validation
        transformed_accounts = []
        for account in settings.EMAIL_ACCOUNTS:
            try:
                # Basic validation of required fields
                if not isinstance(account, dict):
                    logger.error(f"Invalid account data type: {type(account)}")
                    continue

                if "email" not in account:
                    logger.error("Account missing required 'email' field")
                    continue

                # Create Account model instance for validation
                account_model = Account(
                    email=account["email"],
                    name=account.get("name", ""),  # Use get() with default values
                    type=account.get("type", "gmail"),  # Default to gmail
                    categories=account.get("categories", [])  # Default to empty list
                )
                
                # Convert to dict for response
                transformed = account_model.model_dump()
                logger.info(f"Transformed account: {transformed}")
                transformed_accounts.append(transformed)
            except Exception as e:
                logger.error(f"Error transforming account data: {str(e)}")
                if hasattr(e, '__cause__'):
                    logger.error(f"Caused by: {e.__cause__}")
                continue

        duration = time.time() - start_time
        logger.info(f"Fetched {len(transformed_accounts)} accounts in {duration:.2f}s")

        return transformed_accounts

    except Exception as e:
        logger.error(f"Unexpected error in get_accounts: {str(e)}")
        if hasattr(e, '__cause__'):
            logger.error(f"Caused by: {e.__cause__}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get accounts: {str(e)}"
        )

@app.post("/accounts", response_model=TaskResponse)
async def add_account(account: NewAccount):
    """
    Add a new email account.
    The account will be added to the configuration and the email service will be updated.
    Returns a task ID that can be used to track the progress.
    """
    try:
        # Create account dict
        new_account = {
            "email": account.email,
            "password": account.password,
            "type": account.type
        }
        
        # Read current accounts from .env
        with open(".env", "r") as f:
            env_content = f.read()
            
        # Parse the EMAIL_ACCOUNTS line
        import re
        pattern = r'EMAIL_ACCOUNTS=(\[.*?\])'
        match = re.search(pattern, env_content, re.DOTALL)
        if match:
            current_accounts = json.loads(match.group(1))
            
            # Check if account already exists
            if any(acc["email"] == account.email for acc in current_accounts):
                raise HTTPException(
                    status_code=400,
                    detail="Account already exists"
                )
                
            # Add new account
            current_accounts.append(new_account)
            
            # Update .env file
            new_env_content = re.sub(
                pattern,
                f'EMAIL_ACCOUNTS={json.dumps(current_accounts)}',
                env_content,
                flags=re.DOTALL
            )
            
            with open(".env", "w") as f:
                f.write(new_env_content)
            
            # Update settings
            settings.EMAIL_ACCOUNTS = current_accounts
            
            # Start the account addition task
            task_response = await service_factory.email_service.add_account(new_account)
            
            return task_response
            
        else:
            raise HTTPException(
                status_code=500,
                detail="Could not parse EMAIL_ACCOUNTS from .env"
            )
            
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to add account: {str(e)}"
        )

@app.get("/tasks/{task_id}", response_model=TaskStatus)
async def get_task_status(task_id: str):
    """Get the status of a background task"""
    try:
        return await service_factory.email_service.get_task_status(task_id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get task status: {str(e)}"
        )

@app.delete("/accounts/{email}")
async def remove_account(email: str):
    """
    Remove an email account.
    The account will be removed from the configuration and the email service will be updated.
    """
    try:
        # First, clean up emails from the database and search index
        async with AsyncSessionLocal() as session:
            # Get all emails for this account
            query = select(Email).where(Email.account == email)
            result = await session.execute(query)
            emails = result.scalars().all()
            
            # Delete from Elasticsearch first
            for email_obj in emails:
                try:
                    await service_factory.search_service.delete_email(email_obj.message_id)
                except Exception as e:
                    print(f"Error deleting email from search index: {str(e)}")
            
            # Then delete from database
            delete_query = Email.__table__.delete().where(Email.account == email)
            await session.execute(delete_query)
            await session.commit()
            
        # Then remove account from configuration
        with open(".env", "r") as f:
            env_content = f.read()
            
        # Parse the EMAIL_ACCOUNTS line
        import re
        pattern = r'EMAIL_ACCOUNTS=(\[.*?\])'
        match = re.search(pattern, env_content, re.DOTALL)
        if match:
            current_accounts = json.loads(match.group(1))
            
            # Remove account
            new_accounts = [acc for acc in current_accounts if acc["email"] != email]
            
            if len(new_accounts) == len(current_accounts):
                raise HTTPException(
                    status_code=404,
                    detail="Account not found"
                )
            
            # Update .env file
            new_env_content = re.sub(
                pattern,
                f'EMAIL_ACCOUNTS={json.dumps(new_accounts)}',
                env_content,
                flags=re.DOTALL
            )
            
            with open(".env", "w") as f:
                f.write(new_env_content)
            
            # Update settings
            settings.EMAIL_ACCOUNTS = new_accounts
            
            return {"status": "success"}
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to remove account: {str(e)}"
        )

@app.get("/email/{message_id}/content")
async def get_email_content(message_id: str):
    """
    Get the full content of a specific email by its message ID.
    If the content is not in the database, fetches it from the email server.
    """
    try:
        content = await service_factory.email_service.get_email_content(message_id)
        return {"content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/clear-cache")
async def clear_cache():
    """
    Clear all cached data from both database and search index.
    This will remove all stored emails, requiring a fresh fetch from email servers.
    """
    try:
        # Clear Elasticsearch index
        await service_factory.search_service.clear_index()
        
        # Clear database
        async with AsyncSessionLocal() as session:
            # Delete all emails from the database
            await session.execute(delete(Email))
            await session.commit()
            
        return {
            "status": "success",
            "message": "Successfully cleared all cached data. Please reload the application to fetch fresh data."
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to clear cache: {str(e)}"
        )

@app.post("/reindex")
async def reindex_emails():
    """
    Reindex all emails from SQLite to Elasticsearch.
    """
    try:
        # First clear the Elasticsearch index
        await service_factory.search_service.clear_index()
        
        # Get all emails from SQLite
        async with AsyncSessionLocal() as session:
            query = select(Email)
            result = await session.execute(query)
            emails = result.scalars().all()
            
            # Index each email in Elasticsearch
            indexed_count = 0
            for email in emails:
                try:
                    await service_factory.search_service.index_email(email)
                    indexed_count += 1
                except Exception as e:
                    logger.error(f"Error indexing email {email.message_id}: {str(e)}")
                    if hasattr(e, 'info'):
                        logger.error(f"Error info: {e.info}")
            
            return {
                "message": f"Successfully reindexed {indexed_count} out of {len(emails)} emails",
                "total_emails": len(emails),
                "indexed_emails": indexed_count
            }
    except Exception as e:
        logger.error(f"Error during reindexing: {str(e)}")
        if hasattr(e, 'info'):
            logger.error(f"Error info: {e.info}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/email/fetch")
async def fetch_new_emails(background_tasks: BackgroundTasks):
    """Start fetching new emails in the background"""
    try:
        # Start email fetching in background
        background_tasks.add_task(service_factory.email_service.fetch_new_emails)
        return {"message": "Email fetch started in background"}
    except Exception as e:
        logger.error(f"Error starting email fetch: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/email/status")
async def get_email_status():
    """Get status of email fetching process"""
    try:
        status = service_factory.email_service.get_fetch_status()
        return status
    except Exception as e:
        logger.error(f"Error getting email status: {e}")
        raise HTTPException(status_code=500, detail=str(e)) 
import sys
from typing import List, Dict, Optional, Any
import aioimaplib
import asyncio
import email
import ssl
import imaplib
from email.header import decode_header, make_header
from datetime import datetime, timedelta
from ..core.config import settings
from ..models.email import Email
from ..core.database import AsyncSessionLocal
from ..core.task_manager import task_manager, Task
from ..core.interfaces import SearchServiceProtocol, NotificationServiceProtocol
from sqlalchemy import select, text, func, and_
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
import logging
from email.utils import parsedate_to_datetime
import time
import psutil
import gc

class EmailService:
    def __init__(self, search_service: SearchServiceProtocol, notification_service: NotificationServiceProtocol):
        self._logger = logging.getLogger(__name__)
        self._setup_logging()
        self._imap_connections = {}
        self.listeners: Dict[str, asyncio.Task] = {}
        self._search_service = search_service
        self._notification_service = notification_service
        self._logger.info("EmailService initialized")
        self._register_task_handlers()
        # Reduce memory limits
        self._total_processed_size = 0
        self._max_batch_memory = 25 * 1024 * 1024  # 25MB limit per batch
        self._max_email_size = 5 * 1024 * 1024     # 5MB limit per email
        self._batch_size = 3                        # Reduce batch size
        # Add memory tracking
        self._memory_check_interval = 60  # Check memory every 60 seconds
        self._last_memory_check = time.time()
        self._memory_threshold = 75.0  # Percentage

    def _register_task_handlers(self):
        """Register handlers for background tasks"""
        task_manager.register_handler("add_account", self._handle_add_account)
        task_manager.register_handler("fetch_emails", self._handle_fetch_emails)

    async def _handle_add_account(self, task: Task):
        """Background task handler for adding an account"""
        account = task.params
        try:
            # Step 1: Test connection
            task.update(10, "Testing connection...")
            await self.connect_account(account)
            
            # Step 2: Store account credentials (in your secure storage)
            task.update(30, "Storing account credentials...")
            # TODO: Implement secure credential storage
            
            # Step 3: Start email fetch in a separate task
            task.update(50, "Starting initial email fetch...")
            fetch_task = await task_manager.create_task("fetch_emails", {
                "account": account,
                "initial_fetch": True
            })
            
            # Step 4: Setup IDLE listener
            task.update(80, "Setting up email listener...")
            if account["email"] not in self.listeners:
                self.listeners[account["email"]] = asyncio.create_task(
                    self.start_idle_listener(account["email"])
                )
            
            # Complete the task
            task.update(100, "Account added successfully")
            task.result = {
                "email": account["email"],
                "type": account["type"],
                "status": "connected",
                "fetch_task_id": fetch_task.id
            }
            
        except Exception as e:
            task.error = str(e)
            raise

    async def _handle_fetch_emails(self, task: Task):
        """Background task handler for fetching emails"""
        account = task.params["account"]
        is_initial_fetch = task.params.get("initial_fetch", False)
        
        try:
            # Reset memory counter for this fetch
            self._total_processed_size = 0
            
            # Get existing emails for deduplication
            async with AsyncSessionLocal() as session:
                existing_query = select(Email)
                result = await session.execute(existing_query)
                existing_emails = {email.message_id: email for email in result.scalars().all()}
            
            task.update(10, "Connected to email server")
            
            # Configure fetch parameters - reduce initial fetch window if it's first time
            if is_initial_fetch:
                days_ago = 7  # Start with just 7 days for initial fetch
            else:
                days_ago = 30
                
            fetch_date = (datetime.now() - timedelta(days=days_ago)).strftime("%d-%b-%Y")
            search_criteria = f'SINCE {fetch_date}'
            
            # Get message count
            imap_client = self._imap_connections.get(account["email"])
            if not imap_client:
                imap_client = await self.connect_account(account)
            
            select_response = await imap_client.select('INBOX')
            if select_response.result != 'OK':
                raise Exception("Failed to select INBOX")
            
            search_response = await imap_client.search(search_criteria)
            if search_response.result != 'OK':
                raise Exception("Failed to search messages")
            
            message_numbers = search_response.lines[0].decode().split()
            total_messages = len(message_numbers)
            
            task.update(20, f"Found {total_messages} messages to process")
            
            # Process in smaller batches
            batch_size = 5  # Reduced batch size
            processed = 0
            new_emails = 0
            skipped = 0
            
            for i in range(0, total_messages, batch_size):
                # Reset batch memory counter
                batch_memory = 0
                
                batch = message_numbers[i:i + batch_size]
                batch_start = i + 1
                batch_end = min(i + batch_size, total_messages)
                
                task.update(
                    20 + int(60 * (i / total_messages)),
                    f"Processing batch {batch_start}-{batch_end} of {total_messages}"
                )
                
                for email_id in reversed(batch):
                    try:
                        fetch_response = await imap_client.fetch(str(email_id), '(RFC822)')
                        if fetch_response.result != 'OK':
                            continue
                            
                        # Check email size before processing
                        email_size = sum(len(line) if isinstance(line, bytes) else 0 for line in fetch_response.lines)
                        
                        if email_size > self._max_email_size:
                            self._logger.warning(f"Skipping email {email_id} - too large ({email_size} bytes)")
                            skipped += 1
                            continue
                            
                        if batch_memory + email_size > self._max_batch_memory:
                            self._logger.warning("Batch memory limit reached, processing remaining emails in next batch")
                            break
                            
                        batch_memory += email_size
                        self._total_processed_size += email_size
                            
                        email_data = self._extract_email_data(fetch_response.lines)
                        if email_data:
                            # Trim content if it's too large
                            if 'content' in email_data and isinstance(email_data['content'], str):
                                if len(email_data['content'].encode('utf-8')) > 1024 * 1024:  # 1MB content limit
                                    email_data['content'] = email_data['content'][:500000] + "... (content truncated)"
                            
                            stored_email = await self.store_email(email_data, account["email"])
                            if stored_email:
                                new_emails += 1
                                
                    except Exception as e:
                        self._logger.error(f"Error processing email {email_id}: {str(e)}")
                        continue
                        
                processed += len(batch)
                task.update(
                    20 + int(60 * (processed / total_messages)),
                    f"Processed {processed}/{total_messages} messages. Found {new_emails} new emails."
                )
                
                # Add delay between batches to allow memory cleanup
                await asyncio.sleep(1)
                
            task.update(90, "Finalizing...")
            task.result = {
                "total_processed": processed,
                "new_emails": new_emails,
                "skipped": skipped,
                "total_size_mb": round(self._total_processed_size / (1024 * 1024), 2),
                "account": account["email"]
            }
            task.update(100, "Email fetch completed successfully")
            
        except Exception as e:
            task.error = str(e)
            raise

    async def add_account(self, account: Dict[str, str]) -> Dict[str, str]:
        """Start the account addition process"""
        try:
            # Create a background task for account addition
            task = await task_manager.create_task("add_account", account)
            
            # Return immediately with task ID
            return {
                "task_id": task.id,
                "status": "processing",
                "message": "Account addition started"
            }
            
        except Exception as e:
            self._logger.error(f"Error starting account addition: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start account addition: {str(e)}"
            )

    async def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """Get the status of a background task"""
        task = task_manager.get_task(task_id)
        if not task:
            raise HTTPException(
                status_code=404,
                detail=f"Task {task_id} not found"
            )
        
        return task.to_dict()

    def _setup_logging(self):
        """Set up logging configuration for the email service."""
        # Create handlers
        file_handler = logging.FileHandler('email_service.log', mode='a', encoding='utf-8')
        console_handler = logging.StreamHandler(sys.stdout)
        
        # Create formatters and add it to handlers
        log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        formatter = logging.Formatter(log_format)
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        # Add handlers to the logger
        self._logger.addHandler(file_handler)
        self._logger.addHandler(console_handler)
        
        # Set level
        self._logger.setLevel(logging.DEBUG)

    async def test_with_standard_imap(self, host: str, email_addr: str, password: str) -> bool:
        """Test connection using standard imaplib."""
        try:
            self._logger.debug("Testing with standard imaplib...")
            # Enable debug logging
            imaplib.Debug = 4
            
            # Create standard IMAP connection
            with imaplib.IMAP4_SSL(host) as imap:
                self._logger.debug(f"Standard IMAP connected to {host}")
                result = imap.login(email_addr, password)
                self._logger.debug(f"Standard IMAP login result: {result}")
                return result[0] == 'OK'
        except Exception as e:
            self._logger.error(f"Standard IMAP test failed: {str(e)}")
            return False

    async def connect_account(self, account: Dict[str, str]):
        """Connect to an email account using IMAP."""
        email_addr = account["email"]
        password = account["password"]
        account_type = account["type"]

        # Debug: Log account details (excluding full password)
        self._logger.info("\n=== Starting email connection process ===")
        self._logger.info("Account details:")
        self._logger.info(f"  Email: {email_addr}")
        self._logger.info(f"  Password (first 4 chars): {password[:4]}...")
        self._logger.info(f"  Type: {account_type}")

        # Configure server based on account type
        if account_type == "gmail":
            host = "imap.gmail.com"
            port = 993
        elif account_type == "outlook":
            host = "outlook.office365.com"
            port = 993
        else:
            raise ValueError(f"Unsupported email provider: {account_type}")

        try:
            self._logger.info(f"\nAttempting to connect to {host}:{port}")
            self._logger.info(f"Email address: {email_addr}")
            
            # First test with standard imaplib
            self._logger.info("\nTesting with standard imaplib...")
            standard_test = await self.test_with_standard_imap(host, email_addr, password)
            if not standard_test:
                raise Exception("Standard IMAP connection test failed - check your email and password")
            self._logger.info("Standard IMAP test successful")
            
            # If standard test succeeds, proceed with aioimaplib
            self._logger.info("\nProceeding with aioimaplib connection...")
            # Increase timeout to 30 sec
            imap_client = aioimaplib.IMAP4_SSL(host=host, port=port, timeout=30.0)
            self._logger.info("Created IMAP client")
            
            try:
                self._logger.info("Waiting for server hello...")
                hello_response = await asyncio.wait_for(imap_client.wait_hello_from_server(), timeout=30.0)
                self._logger.info(f"Server hello response: {hello_response}")
                
                self._logger.info(f"\nAttempting login for {email_addr}")
                login_response = await asyncio.wait_for(imap_client.login(email_addr, password), timeout=30.0)
                self._logger.info(f"Login response: {login_response}")
                
                if login_response.result != 'OK':
                    error_msg = login_response.lines[0].decode() if login_response.lines else "Unknown error"
                    raise Exception(f"Login failed with response: {login_response}, Error: {error_msg}")
                
                # Test INBOX access
                self._logger.info("\nTesting INBOX access...")
                select_response = await asyncio.wait_for(imap_client.select('INBOX'), timeout=30.0)
                self._logger.info(f"Select INBOX response: {select_response}")
                
                if select_response.result != 'OK':
                    raise Exception(f"Failed to access INBOX: {select_response}")
                
                # Get mailbox status
                status_response = await imap_client.status('INBOX', '(MESSAGES RECENT UNSEEN)')
                self._logger.info(f"Mailbox status: {status_response}")
                
                # Store the connection in self.connections
                self._imap_connections[email_addr] = imap_client
                self._logger.info(f"\nSuccessfully stored connection for {email_addr}")
                
                self._logger.info("\n=== Email connection process completed successfully ===")
                return imap_client
                
            except asyncio.TimeoutError as e:
                self._logger.error("\n[ERROR] Connection timed out")
                self._logger.error(f"Error type: {type(e).__name__}")
                self._logger.error(f"Error details: {str(e)}")
                if hasattr(e, '__cause__'):
                    self._logger.error(f"Caused by: {e.__cause__}")
                try:
                    await imap_client.logout()
                except:
                    pass
                raise Exception("Connection timed out while establishing IMAP connection")
                
            except Exception as e:
                self._logger.error("\n[ERROR] Connection setup failed")
                self._logger.error(f"Error type: {type(e).__name__}")
                self._logger.error(f"Error details: {str(e)}")
                if hasattr(e, '__cause__'):
                    self._logger.error(f"Caused by: {e.__cause__}")
                try:
                    await imap_client.logout()
                except:
                    pass
                raise e
                
        except Exception as e:
            self._logger.error("\n[ERROR] Connection process failed")
            self._logger.error(f"Error type: {type(e).__name__}")
            self._logger.error(f"Error details: {str(e)}")
            if hasattr(e, '__cause__'):
                self._logger.error(f"Caused by: {e.__cause__}")
            raise Exception(f"Connection failed: {str(e)}")

    async def test_connection(self) -> Dict[str, str]:
        """Test connection to email account."""
        if not settings.EMAIL_ACCOUNTS:
            raise ValueError("No email accounts configured")
        
        account = settings.EMAIL_ACCOUNTS[0]
        try:
            self._logger.info(f"Testing connection for {account['email']}")
            # Connect and authenticate
            imap_client = await self.connect_account(account)
            self._logger.info("Connection successful, selecting INBOX")
            
            # Select INBOX to verify connection
            response = await imap_client.select('INBOX')
            self._logger.info(f"Select INBOX response: {response}")
            
            if response.result != 'OK':
                raise Exception(f"Failed to select INBOX: {response}")
            
            return {
                "email": account["email"],
                "status": "connected",
                "mailbox": "INBOX"
            }
        except Exception as e:
            self._logger.error(f"Test connection error: {str(e)}")
            raise Exception(f"Failed to connect to email: {str(e)}")

    def _parse_email_date(self, date_str: str) -> str:
        """Convert email date string to ISO format."""
        try:
            # Parse the email date string to datetime object
            dt = parsedate_to_datetime(date_str)
            # Convert to ISO format string
            return dt.isoformat()
        except Exception as e:
            self._logger.error(f"Error parsing date {date_str}: {e}")
            return None

    async def store_email(self, email_data: Dict[str, str], account: str):
        """Store email in SQLite database and index in Elasticsearch."""
        try:
            self._logger.info("\n=== Starting email storage process ===")
            self._logger.info(f"Storing email: {email_data.get('subject', 'No subject')}")
            self._logger.info(f"Account: {account}")

            # Parse the date to ISO format
            date_str = email_data.get('date')
            if date_str:
                iso_date = self._parse_email_date(date_str)
                if iso_date:
                    email_data['date'] = iso_date
                else:
                    self._logger.error(f"Could not parse date: {date_str}")
                    return None

            message_id = email_data.get('message_id', '').strip('<>')
            
            async with AsyncSessionLocal() as session:
                try:
                    # Check if email already exists
                    existing = await session.execute(
                        select(Email).where(Email.message_id == message_id)
                    )
                    if existing.scalar_one_or_none():
                        self._logger.info(f"Email {message_id} already exists in database")
                        return None

                    # Create new email
                    email = Email(
                        message_id=message_id,
                        account=account,
                        subject=email_data.get('subject'),
                        sender=email_data.get('sender'),
                        date=email_data.get('date'),
                        content=email_data.get('content'),
                        is_read=False,
                        category=None,
                        created_at=datetime.now(),
                        updated_at=datetime.now()
                    )
                    session.add(email)
                    await session.commit()
                    self._logger.info("Email stored successfully")
                    return email

                except Exception as e:
                    self._logger.error(f"Database error: {str(e)}")
                    self._logger.error(f"Caused by: {e.__cause__}")
                    await session.rollback()
                    return None

        except Exception as e:
            self._logger.error(f"Error storing email: {str(e)}")
            return None

    async def get_cached_emails(self, limit: int = 5) -> List[Dict[str, str]]:
        """Get recent emails from SQLite cache."""
        try:
            async with AsyncSessionLocal() as session:
                query = select(Email).order_by(Email.date.desc()).limit(limit)
                result = await session.execute(query)
                emails = result.scalars().all()
                return [email.to_dict() for email in emails]
        except Exception as e:
            self._logger.error(f"Error fetching cached emails: {str(e)}")
            return []

    async def get_recent_emails(self, offset: int = 0, limit: int = 20) -> Dict[str, any]:
        """Get emails from the last 30 days from all configured accounts with pagination."""
        if not settings.EMAIL_ACCOUNTS:
            self._logger.error("No email accounts configured")
            raise ValueError("No email accounts configured")
        
        # Get list of currently configured account emails
        current_accounts = [acc["email"] for acc in settings.EMAIL_ACCOUNTS]
        self._logger.info("\n=== Fetching recent emails ===")
        self._logger.info(f"Accounts: {current_accounts}")
        self._logger.info(f"Offset: {offset}, Limit: {limit}")
        
        try:
            async with AsyncSessionLocal() as session:
                try:
                    # Verify database connection
                    await session.execute(text("SELECT 1"))
                    self._logger.info("Database connection verified")
                    
                    # Get total count for pagination
                    count_query = select(func.count()).select_from(Email).where(
                        and_(
                            Email.account.in_(current_accounts),
                            Email.date >= datetime.now() - timedelta(days=30)
                        )
                    )
                    total_count = await session.scalar(count_query) or 0
                    self._logger.info(f"Total emails in database: {total_count}")
                    
                    # If no emails in database, trigger a fetch
                    if total_count == 0:
                        self._logger.info("No emails found in database, triggering fetch...")
                        # Get existing emails for deduplication
                        existing_query = select(Email)
                        result = await session.execute(existing_query)
                        existing_emails = {email.message_id: email for email in result.scalars().all()}
                        
                        # Fetch new emails
                        await self._fetch_and_process_new_emails(existing_emails)
                        
                        # Recount after fetch
                        total_count = await session.scalar(count_query) or 0
                        self._logger.info(f"After fetch, total emails in database: {total_count}")
                    
                    # Build paginated query
                    query = (
                        select(Email)
                        .where(
                            and_(
                                Email.account.in_(current_accounts),
                                Email.date >= datetime.now() - timedelta(days=30)
                            )
                        )
                        .order_by(Email.date.desc())
                        .offset(offset)
                        .limit(limit)
                    )
                    
                    try:
                        self._logger.info("Executing paginated query...")
                        result = await session.execute(query)
                        cached_emails = result.scalars().all()
                        self._logger.info(f"Retrieved {len(cached_emails)} emails from database")
                        
                        # Convert to list of dictionaries with validation
                        emails = []
                        for email in cached_emails:
                            try:
                                email_dict = email.to_dict()
                                # Validate required fields
                                required_fields = ['message_id', 'subject', 'sender', 'date', 'account']
                                if all(field in email_dict for field in required_fields):
                                    emails.append(email_dict)
                                else:
                                    self._logger.warning(f"Email {email.id} missing required fields")
                            except Exception as e:
                                self._logger.error(f"Failed to convert email {email.id} to dict: {str(e)}")
                        
                        self._logger.info(f"Successfully processed {len(emails)} valid emails")
                        
                        # Verify IMAP connections are still valid
                        for account in settings.EMAIL_ACCOUNTS:
                            if account["email"] not in self._imap_connections:
                                self._logger.info(f"Reconnecting to account {account['email']}")
                                try:
                                    await self.connect_account(account)
                                except Exception as e:
                                    self._logger.error(f"Failed to reconnect to {account['email']}: {str(e)}")
                        
                        # Log first email details for debugging
                        if emails:
                            self._logger.info("\nFirst email details:")
                            self._logger.info(f"  ID: {emails[0].get('id')}")
                            self._logger.info(f"  Message ID: {emails[0].get('message_id')}")
                            self._logger.info(f"  Subject: {emails[0].get('subject')}")
                            self._logger.info(f"  Account: {emails[0].get('account')}")
                            self._logger.info(f"  Date: {emails[0].get('date')}")
                        
                        response = {
                            "emails": emails,
                            "total": total_count,
                            "hasMore": offset + limit < total_count
                        }
                        
                        self._logger.info("\n=== Email fetch summary ===")
                        self._logger.info(f"Total emails in response: {len(emails)}")
                        self._logger.info(f"Has more: {response['hasMore']}")
                        self._logger.info(f"Total in database: {total_count}")
                        
                        return response
                        
                    except Exception as e:
                        self._logger.error(f"Failed to execute paginated query: {str(e)}")
                        if hasattr(e, '__cause__'):
                            self._logger.error(f"Caused by: {e.__cause__}")
                        raise
                        
                except Exception as e:
                    self._logger.error(f"Database session error: {str(e)}")
                    if hasattr(e, '__cause__'):
                        self._logger.error(f"Caused by: {e.__cause__}")
                    raise
                    
        except Exception as e:
            self._logger.error(f"Fatal error in get_recent_emails: {str(e)}")
            if hasattr(e, '__cause__'):
                self._logger.error(f"Caused by: {e.__cause__}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch recent emails: {str(e)}"
            )

    async def _fetch_and_process_new_emails(self, existing_emails: Dict[str, Email]):
        """Background task to fetch and process new emails."""
        self._logger.info("\n=== Starting email fetch process ===")
        self._logger.info(f"Currently have {len(existing_emails)} emails in database")
        
        try:
            for account in settings.EMAIL_ACCOUNTS:
                try:
                    self._logger.info(f"\n=== Processing emails for account {account['email']} ===")
                    self._logger.info("Account details:")
                    self._logger.info(f"  Email: {account['email']}")
                    self._logger.info(f"  Password (first 4 chars): {account['password'][:4]}...")
                    self._logger.info(f"  Type: {account['type']}")
                    
                    # Connect to IMAP server
                    self._logger.info("\nConnecting to IMAP server...")
                    imap_client = await self.connect_account(account)
                    self._logger.info("Successfully connected to IMAP server")
                    
                    try:
                        # Select INBOX with timeout
                        self._logger.info("\nSelecting INBOX...")
                        select_response = await asyncio.wait_for(imap_client.select('INBOX'), timeout=30.0)
                        self._logger.info(f"Select INBOX response: {select_response}")
                        
                        if select_response.result != 'OK':
                            raise Exception(f"Failed to select INBOX: {select_response}")
                        
                        # Search for emails from the last 30 days
                        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime("%d-%b-%Y")
                        self._logger.info(f"\nSearching for emails since {thirty_days_ago}")
                        
                        search_response = await asyncio.wait_for(
                            imap_client.search(f'SINCE {thirty_days_ago}'),
                            timeout=30.0
                        )
                        self._logger.info(f"Search response: {search_response}")
                        
                        if search_response.result != 'OK':
                            raise Exception(f"Search failed: {search_response}")
                        
                        message_numbers = search_response.lines[0].decode().split()
                        total_messages = len(message_numbers)
                        self._logger.info(f"Found {total_messages} messages")
                        
                        if not message_numbers:
                            self._logger.info("No messages found in search range")
                            continue
                        
                        # Process emails in batches
                        batch_size = 10  # Reduced batch size for better reliability
                        processed = 0
                        new_emails = 0
                        skipped = 0
                        
                        for i in range(0, total_messages, batch_size):
                            batch = message_numbers[i:i + batch_size]
                            batch_start = i + 1
                            batch_end = min(i + batch_size, total_messages)
                            self._logger.info(f"\nProcessing batch {batch_start}-{batch_end} of {total_messages}")
                            
                            for email_id in reversed(batch):
                                processed += 1
                                try:
                                    self._logger.info(f"\nProcessing email {email_id} ({processed}/{total_messages})")
                                    
                                    # Fetch email content with retry
                                    max_retries = 3
                                    retry_count = 0
                                    fetch_response = None
                                    
                                    while retry_count < max_retries:
                                        try:
                                            self._logger.info(f"Fetch attempt {retry_count + 1} for email {email_id}")
                                            fetch_response = await asyncio.wait_for(
                                                imap_client.fetch(str(email_id), '(RFC822)'),
                                                timeout=30.0
                                            )
                                            if fetch_response.result == 'OK':
                                                self._logger.info("Fetch successful")
                                                break
                                            self._logger.warning(f"Fetch attempt {retry_count + 1} failed with result: {fetch_response.result}")
                                            retry_count += 1
                                            await asyncio.sleep(1)
                                        except Exception as e:
                                            self._logger.error(f"Fetch attempt {retry_count + 1} failed with error: {str(e)}")
                                            retry_count += 1
                                            if retry_count < max_retries:
                                                await asyncio.sleep(1)
                                            else:
                                                raise
                                    
                                    if not fetch_response or fetch_response.result != 'OK':
                                        self._logger.error(f"Failed to fetch email {email_id} after {max_retries} attempts")
                                        continue
                                    
                                    # Extract and process email data
                                    email_data = None
                                    self._logger.info("Extracting email data from response...")
                                    for i, line in enumerate(fetch_response.lines):
                                        if isinstance(line, bytes):
                                            if b'BODY[]' in line or b'RFC822' in line:
                                                # The next line should be the email data
                                                if i + 1 < len(fetch_response.lines):
                                                    email_data = fetch_response.lines[i + 1]
                                                    self._logger.info("Found email data in response")
                                                    break
                                    
                                    if not email_data:
                                        self._logger.error(f"No data found for email {email_id}")
                                        continue
                                    
                                    # Parse email message
                                    self._logger.info("Parsing email message...")
                                    msg = email.message_from_bytes(email_data)
                                    if not msg:
                                        self._logger.error(f"Failed to parse email {email_id}")
                                        continue
                                    
                                    # Extract email data
                                    try:
                                        self._logger.info("Extracting email headers...")
                                        subject_header = msg.get("subject", "No Subject")
                                        subject = str(make_header(decode_header(subject_header)))
                                        self._logger.info(f"Subject: {subject}")
                                    except Exception as e:
                                        self._logger.error(f"Error extracting subject: {str(e)}")
                                        subject = "No Subject"
                                    
                                    try:
                                        from_header = msg.get("from", "Unknown Sender")
                                        sender = str(make_header(decode_header(from_header)))
                                        self._logger.info(f"Sender: {sender}")
                                    except Exception as e:
                                        self._logger.error(f"Error extracting sender: {str(e)}")
                                        sender = "Unknown Sender"
                                    
                                    message_id = msg.get("message-id", "").strip("<>") or f"temp_{datetime.now().timestamp()}"
                                    self._logger.info(f"Message ID: {message_id}")
                                    
                                    # Skip if email already exists
                                    if message_id in existing_emails:
                                        self._logger.info(f"Email {message_id} already exists, skipping")
                                        skipped += 1
                                        continue
                                    
                                    # Extract body
                                    self._logger.info("Extracting email body...")
                                    content = ""
                                    if msg.is_multipart():
                                        self._logger.info("Processing multipart message...")
                                        for part in msg.walk():
                                            if part.get_content_type() == "text/html" and not part.get_filename():
                                                content = part.get_payload(decode=True).decode('utf-8', errors='replace')
                                                self._logger.info("Found HTML content")
                                                break
                                        if not content:
                                            self._logger.info("No HTML content found, looking for plain text...")
                                            for part in msg.walk():
                                                if part.get_content_type() == "text/plain" and not part.get_filename():
                                                    content = part.get_payload(decode=True).decode('utf-8', errors='replace')
                                                    content = f"<pre style='white-space: pre-wrap;'>{content}</pre>"
                                                    self._logger.info("Found plain text content")
                                                    break
                                    else:
                                        self._logger.info("Processing non-multipart message...")
                                        content = msg.get_payload(decode=True).decode('utf-8', errors='replace')
                                        if msg.get_content_type() != "text/html":
                                            content = f"<pre style='white-space: pre-wrap;'>{content}</pre>"
                                            self._logger.info("Converted plain text to HTML")
                                    
                                    if not content:
                                        self._logger.warning("No content found in email")
                                    
                                    # Create email data
                                    self._logger.info("Creating email data dictionary...")
                                    email_data = {
                                        "message_id": message_id,
                                        "subject": subject,
                                        "sender": sender,
                                        "date": msg.get("date", datetime.now().strftime("%a, %d %b %Y %H:%M:%S %z")),
                                        "content": content,
                                        "account": account["email"]
                                    }
                                    
                                    # Store email
                                    self._logger.info("Storing email in database...")
                                    stored_email = await self.store_email(email_data, account["email"])
                                    if stored_email:
                                        new_emails += 1
                                        self._logger.info(f"Successfully processed and stored email {new_emails}")
                                    else:
                                        skipped += 1
                                        self._logger.warning("Failed to store email")
                                    
                                except Exception as e:
                                    self._logger.error(f"Failed to process email {email_id}: {str(e)}")
                                    if hasattr(e, '__cause__'):
                                        self._logger.error(f"Caused by: {e.__cause__}")
                                    continue
                            
                            self._logger.info(f"\nBatch complete. Progress: {processed}/{total_messages}")
                            self._logger.info(f"New emails in this batch: {new_emails}")
                            self._logger.info(f"Skipped in this batch: {skipped}")
                            
                            # Small delay between batches to avoid overwhelming the server
                            await asyncio.sleep(0.5)
                        
                        self._logger.info(f"\n=== Email processing complete for {account['email']} ===")
                        self._logger.info(f"Total messages found: {total_messages}")
                        self._logger.info(f"Successfully processed: {new_emails}")
                        self._logger.info(f"Skipped (already exists): {skipped}")
                        self._logger.info(f"Failed to process: {total_messages - (new_emails + skipped)}")
                        
                    except Exception as e:
                        self._logger.error(f"Failed to process emails for account {account['email']}: {str(e)}")
                        if hasattr(e, '__cause__'):
                            self._logger.error(f"Caused by: {e.__cause__}")
                    finally:
                        if imap_client:
                            try:
                                await imap_client.close()
                                await imap_client.logout()
                                self._logger.info(f"Successfully closed connection for {account['email']}")
                                if account["email"] in self._imap_connections:
                                    await self._imap_connections[account["email"]].logout()
                                    del self._imap_connections[account["email"]]
                            except Exception as e:
                                self._logger.error(f"Error during connection cleanup: {str(e)}")
                
                except Exception as e:
                    self._logger.error(f"Failed to process account {account['email']}: {str(e)}")
                    if hasattr(e, '__cause__'):
                        self._logger.error(f"Caused by: {e.__cause__}")
                    continue
            
            self._logger.info("\n=== Email fetch process complete ===")
            self._logger.info(f"Processed {len(settings.EMAIL_ACCOUNTS)} accounts")
            
        except Exception as e:
            self._logger.error(f"Fatal error in email fetch process: {str(e)}")
            if hasattr(e, '__cause__'):
                self._logger.error(f"Caused by: {e.__cause__}")
            raise

    def _extract_header_data(self, lines) -> Optional[bytes]:
        """Extract header data from IMAP response lines."""
        header_lines = []
        in_headers = False
        
        for line in lines:
            if isinstance(line, bytes):
                if b'RFC822.HEADER' in line:  # Changed from FETCH
                    in_headers = True
                    continue
                elif line == b')':
                    in_headers = False
                    continue
                
                if in_headers:
                    header_lines.append(line)
                    
        if not header_lines:
            # Try alternative format
            for line in lines:
                if isinstance(line, bytes) and not (line.startswith(b'*') or line.startswith(b')')):
                    header_lines.append(line)
                    
        return b'\r\n'.join(header_lines) if header_lines else None
        
    def _parse_email_headers(self, msg) -> Optional[Dict[str, str]]:
        """Parse email headers into a dictionary."""
        try:
            self._logger.info("\nParsing email headers...")
            self._logger.info(f"Raw headers: {dict(msg)}")
            
            # Extract and decode subject
            subject = "No Subject"
            if msg["subject"]:
                subject_parts = decode_header(msg["subject"])
                self._logger.info(f"Subject parts: {subject_parts}")
                subject = ""
                for part, charset in subject_parts:
                    self._logger.info(f"Processing subject part: {part}, charset: {charset}")
                    if isinstance(part, bytes):
                        try:
                            subject += part.decode(charset or 'utf-8', errors='replace')
                        except (LookupError, TypeError):
                            subject += part.decode('utf-8', errors='replace')
                    else:
                        subject += str(part)
            self._logger.info(f"Final subject: {subject}")
            
            # Extract and decode sender
            sender = "Unknown Sender"
            if msg["from"]:
                from_parts = decode_header(msg["from"])
                self._logger.info(f"From parts: {from_parts}")
                sender = ""
                for part, charset in from_parts:
                    self._logger.info(f"Processing sender part: {part}, charset: {charset}")
                    if isinstance(part, bytes):
                        try:
                            sender += part.decode(charset or 'utf-8', errors='replace')
                        except (LookupError, TypeError):
                            sender += part.decode('utf-8', errors='replace')
                    else:
                        sender += str(part)
            self._logger.info(f"Final sender: {sender}")
            
            result = {
                "subject": subject,
                "sender": sender,
                "date": msg.get("date", ""),
                "message_id": msg.get("message-id", "").strip("<>")
            }
            self._logger.info(f"Final parsed headers: {result}")
            return result
            
        except Exception as e:
            self._logger.error(f"Error parsing email headers: {str(e)}")
            return None

    async def start_idle_listener(self, email: str):
        """Start IDLE listener for new emails."""
        if email not in self._imap_connections:
            raise ValueError(f"No connection found for {email}")

        imap_client = self._imap_connections[email]
        
        while True:
            try:
                # Select INBOX
                select_response = await imap_client.select("INBOX")
                if select_response.result != 'OK':
                    raise Exception(f"Failed to select INBOX: {select_response.result}")
                
                self._logger.info(f"Starting IDLE listener for {email}")
                
                while True:
                    try:
                        # Start IDLE mode
                        idle = await imap_client.idle_start()
                        self._logger.info(f"IDLE mode started for {email}")
                        
                        # Wait for new email notifications
                        response = await imap_client.wait_server_push()
                        
                        if response is None:
                            self._logger.info(f"No response from server for {email}, restarting IDLE")
                            continue
                            
                        # Stop IDLE mode
                        await imap_client.idle_done()
                        
                        # Process the response
                        for line in response:
                            if b'EXISTS' in line:
                                self._logger.info(f"New email detected for {email}")
                                # Get the latest emails
                                await self._process_new_emails(email)
                                break
                                
                    except Exception as e:
                        self._logger.error(f"Error in IDLE loop for {email}: {e}")
                        # Break inner loop to reconnect
                        break
                        
            except Exception as e:
                self._logger.error(f"Error in IDLE listener for {email}: {e}")
                # Attempt to reconnect
                await asyncio.sleep(5)
                try:
                    await self.connect_account({"email": email, "type": "gmail"})  # You'll need to store account type
                except Exception as conn_error:
                    self._logger.error(f"Failed to reconnect {email}: {conn_error}")
                    await asyncio.sleep(30)  # Wait longer before next retry

    async def _process_new_emails(self, email: str):
        """Process new emails when IDLE notifies of changes."""
        try:
            # Search for all emails from the last known timestamp
            last_email = await self._get_last_email_date(email)
            search_criteria = f'SINCE "{last_email.strftime("%d-%b-%Y")}"'
            
            imap_client = self._imap_connections[email]
            search_response = await imap_client.search(search_criteria)
            
            if search_response.result != 'OK':
                raise Exception(f"Search failed: {search_response}")
                
            message_numbers = search_response.lines[0].decode().split()
            
            for number in message_numbers:
                try:
                    # Fetch the email
                    fetch_response = await imap_client.fetch(number, '(RFC822)')
                    if fetch_response.result != 'OK':
                        continue
                        
                    # Process the email
                    email_data = self._extract_email_data(fetch_response.lines)
                    if email_data:
                        # Store in database and notify clients
                        await self._store_and_notify(email_data)
                        
                except Exception as e:
                    self._logger.error(f"Error processing message {number}: {e}")
                    continue
                    
        except Exception as e:
            self._logger.error(f"Error processing new emails: {e}")

    async def _get_last_email_date(self, email: str) -> datetime:
        """Get the date of the last email we have for this account."""
        async with AsyncSessionLocal() as session:
            query = select(Email).where(
                Email.account == email
            ).order_by(Email.date.desc()).limit(1)
            
            result = await session.execute(query)
            last_email = result.scalar_one_or_none()
            
            if last_email:
                return last_email.date
            return datetime.now() - timedelta(days=1)  # Default to 1 day ago

    async def _store_and_notify(self, email_data: Dict):
        """Store email in database and notify connected clients."""
        try:
            # Store in database
            async with AsyncSessionLocal() as session:
                email = Email(**email_data)
                session.add(email)
                await session.commit()
                
            # Notify clients through your notification service
            await self._notification_service.notify_new_email(email_data)
            
        except Exception as e:
            self._logger.error(f"Error storing and notifying: {e}")

    async def start_all_listeners(self):
        """Start IDLE listeners for all configured accounts."""
        for account in settings.EMAIL_ACCOUNTS:
            email = account["email"]
            if email not in self._imap_connections:
                await self.connect_account(account)
            
            if email not in self.listeners:
                self.listeners[email] = asyncio.create_task(
                    self.start_idle_listener(email)
                )

    async def stop_all_listeners(self):
        """Stop all IDLE listeners and close connections."""
        for email, listener in self.listeners.items():
            listener.cancel()
            if email in self._imap_connections:
                await self._imap_connections[email].logout()
        
        self.listeners.clear()
        self._imap_connections.clear()

    async def get_email_content(self, message_id: str) -> str:
        """Get the content of a specific email by its message ID."""
        try:
            # Clean the message ID (remove any existing angle brackets)
            clean_message_id = message_id.strip("<>")
            
            # First try to get from database
            async with AsyncSessionLocal() as session:
                query = select(Email).where(Email.message_id == clean_message_id)
                result = await session.execute(query)
                email = result.scalar_one_or_none()
                
                if email and email.content:
                    return email.content
                    
                # If not in database or no content, fetch from server
                for account in settings.EMAIL_ACCOUNTS:
                    try:
                        imap_client = await self.connect_account(account)
                        
                        # Select INBOX
                        await imap_client.select('INBOX')
                        
                        # Try multiple message ID formats
                        search_formats = [
                            f'HEADER Message-ID "{clean_message_id}"',
                            f'HEADER Message-ID "<{clean_message_id}>"',
                            f'HEADER Message-ID "{clean_message_id}@%"'
                        ]
                        
                        message_numbers = []
                        for search_query in search_formats:
                            try:
                                search_response = imap_client.search(search_query)
                                if search_response.result == 'OK':
                                    numbers = search_response.lines[0].decode().split()
                                    if numbers:
                                        message_numbers = numbers
                                        break
                            except Exception as e:
                                self._logger.error(f"Search failed for format {search_query}: {e}")
                                continue
                        
                        if not message_numbers:
                            continue
                            
                        # Fetch the email content
                        fetch_response = await imap_client.fetch(message_numbers[0], '(RFC822)')
                        
                        if fetch_response.result != 'OK':
                            self._logger.error(f"Fetch failed for message {message_numbers[0]}")
                            continue
                            
                        # Extract content from the response
                        content = None
                        email_body = None
                        
                        # Join all lines and parse as email
                        email_data = b'\r\n'.join(line for line in fetch_response.lines if isinstance(line, bytes))
                        if email_data:
                            try:
                                msg = email.message_from_bytes(email_data)
                                
                                # Handle multipart messages
                                if msg.is_multipart():
                                    # First try to find HTML content
                                    for part in msg.walk():
                                        if part.get_content_type() == "text/html":
                                            email_body = part.get_payload(decode=True).decode('utf-8', errors='replace')
                                            break
                                    
                                    # If no HTML found, try to find plain text
                                    if not email_body:
                                        for part in msg.walk():
                                            if part.get_content_type() == "text/plain":
                                                email_body = part.get_payload(decode=True).decode('utf-8', errors='replace')
                                                # Convert plain text to simple HTML
                                                email_body = f"<pre style='white-space: pre-wrap;'>{email_body}</pre>"
                                                break
                                else:
                                    # Handle non-multipart messages
                                    email_body = msg.get_payload(decode=True).decode('utf-8', errors='replace')
                                    if msg.get_content_type() != "text/html":
                                        email_body = f"<pre style='white-space: pre-wrap;'>{email_body}</pre>"
                                
                                if email_body:
                                    content = email_body
                                    
                            except Exception as e:
                                self._logger.error(f"Error parsing email content: {e}")
                                continue
                        
                        if content:
                            # Update the database with the content
                            if email:
                                email.content = content
                                email.updated_at = datetime.utcnow()
                                await session.commit()
                            return content
                            
                    except Exception as e:
                        self._logger.error(f"Error fetching content from account {account['email']}: {e}")
                        continue
                    finally:
                        # Clean up the connection
                        if account["email"] in self._imap_connections:
                            await self._imap_connections[account["email"]].logout()
                            del self._imap_connections[account["email"]]
                
                raise HTTPException(
                    status_code=404,
                    detail=f"Email content not found for message ID: {clean_message_id}"
                )
                
        except Exception as e:
            self._logger.error(f"Error getting email content: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to retrieve email content: {str(e)}"
            )

    async def fetch_emails_for_account(self, account: Dict[str, str]):
        """Fetch emails for a specific account immediately."""
        try:
            self._logger.info(f"\n=== Fetching emails for new account {account['email']} ===")
            # Use empty dict since we want to fetch all emails for new account
            await self._fetch_and_process_new_emails({})
            self._logger.info(f"=== Completed initial fetch for {account['email']} ===\n")
        except Exception as e:
            self._logger.error(f"Error during initial fetch for {account['email']}: {str(e)}")
            raise

    def _check_memory_usage(self):
        """Check memory usage and cleanup if necessary"""
        current_time = time.time()
        if current_time - self._last_memory_check >= self._memory_check_interval:
            try:
                memory_percent = psutil.Process().memory_percent()
                if memory_percent > self._memory_threshold:
                    self._logger.warning(f"High memory usage detected: {memory_percent}%")
                    gc.collect()
                    self._total_processed_size = 0
            except Exception as e:
                self._logger.error(f"Error checking memory: {e}")
            finally:
                self._last_memory_check = current_time

def get_email_service() -> EmailService:
    """Get the email service instance from the service factory"""
    from ..core.service_factory import service_factory
    return service_factory.email_service 
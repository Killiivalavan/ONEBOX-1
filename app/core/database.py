from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import text
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get the current directory
current_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Create the database URL
SQLALCHEMY_DATABASE_URL = f"sqlite+aiosqlite:///{current_dir}/emails.db"

# Create async engine
engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL,
    echo=True,  # Set to False in production
    future=True,
    connect_args={"check_same_thread": False}  # Needed for SQLite
)

# Create Base class for models
Base = declarative_base()

# Create async session factory
AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def init_db():
    """Initialize the database with proper error handling."""
    try:
        # Import models here to ensure they are registered with Base
        from ..models.email import Email  # noqa
        
        logger.info("Creating database tables...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created successfully!")
        
        # Verify tables were created
        async with engine.connect() as conn:
            # Check if emails table exists using proper SQLAlchemy text()
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='emails'")
            )
            if result.scalar():
                logger.info("Verified 'emails' table exists")
            else:
                logger.error("Failed to create 'emails' table!")
                raise Exception("Failed to create 'emails' table")
            
    except Exception as e:
        logger.error(f"Error initializing database: {str(e)}")
        raise

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close() 
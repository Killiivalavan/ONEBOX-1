import json
from typing import List, Dict
from pydantic_settings import BaseSettings
import os

class Settings(BaseSettings):
    # Server Settings
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True

    # Email Settings
    EMAIL_ACCOUNTS: List[Dict[str, str]] = []

    # Groq AI Settings
    GROQ_API_KEY: str

    # Slack Settings
    SLACK_WEBHOOK_URL: str | None = None

    # Elasticsearch Settings
    # Use localhost when running locally, elasticsearch when in Docker
    ELASTICSEARCH_HOST: str = os.getenv(
        "ELASTICSEARCH_HOST", 
        "http://localhost:9200"  # Default to localhost for local development
    )
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        # Allow environment variables to override
        env_prefix = ""
        # Allow extra fields
        extra = "allow"

        @classmethod
        def parse_env_var(cls, field_name: str, raw_val: str):
            if field_name == "EMAIL_ACCOUNTS":
                try:
                    parsed = json.loads(raw_val)
                    print(f"Parsed EMAIL_ACCOUNTS: {parsed}")
                    return parsed
                except Exception as e:
                    print(f"Error parsing EMAIL_ACCOUNTS: {e}")
                    print(f"Raw value: {raw_val}")
                    raise
            return raw_val

settings = Settings()
print("Loaded settings EMAIL_ACCOUNTS:", settings.EMAIL_ACCOUNTS) 
import aiohttp
from typing import List, Dict
from ..core.config import settings
from ..utils.rate_limiter import RateLimiter
from fastapi import HTTPException

class AIService:
    def __init__(self):
        self.api_key = settings.GROQ_API_KEY
        self.base_url = "https://api.groq.com/openai/v1"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        self.model = "llama-3.1-8b-instant"
        # Initialize rate limiter with 28 requests per minute (slightly under the 30 RPM limit)
        # and a burst size of 5 to handle concurrent requests
        self.rate_limiter = RateLimiter(tokens_per_second=28/60, bucket_size=5)

    async def classify_email(self, email_text: str) -> str:
        """Classify email using Groq API."""
        # Wait for rate limit token
        if not await self.rate_limiter.wait_for_token(timeout=5.0):
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Please try again later."
            )
            
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": """You are an email classifier. Classify the following email into exactly one of these categories:
- 'Interested': Emails showing positive interest or engagement
- 'Meeting Booked': Emails confirming or scheduling meetings
- 'Not Interested': Emails explicitly showing lack of interest
- 'Spam': Promotional or unwanted emails
- 'Out of Office': Auto-replies indicating absence
Only respond with the exact category name."""
                        },
                        {
                            "role": "user",
                            "content": email_text
                        }
                    ],
                    "temperature": 0.1  # Lower temperature for more consistent results
                }
            ) as response:
                if response.status != 200:
                    raise Exception(f"Groq API error: {await response.text()}")
                result = await response.json()
                category = result["choices"][0]["message"]["content"].strip()
                
                # Validate the category is one of the expected values
                valid_categories = {'Interested', 'Meeting Booked', 'Not Interested', 'Spam', 'Out of Office'}
                if category not in valid_categories:
                    print(f"Warning: AI returned invalid category '{category}', defaulting to 'Uncategorized'")
                    return "Uncategorized"
                    
                return category

    async def generate_reply(self, email_text: str, context: Dict[str, str]) -> str:
        """Generate email reply using Groq API."""
        # Wait for rate limit token
        if not await self.rate_limiter.wait_for_token(timeout=5.0):
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Please try again later."
            )
            
        system_prompt = f"""You are an AI assistant helping to generate email replies.
        Context for this email: {context}
        Generate a professional and contextually appropriate reply."""

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": system_prompt
                        },
                        {
                            "role": "user",
                            "content": f"Generate a reply to this email:\n\n{email_text}"
                        }
                    ]
                }
            ) as response:
                if response.status != 200:
                    raise Exception(f"Groq API error: {await response.text()}")
                result = await response.json()
                return result["choices"][0]["message"]["content"].strip()

    async def get_embeddings(self, text: str) -> List[float]:
        """Get text embeddings for similarity search."""
        # Note: If Groq adds embedding support in the future, implement it here
        raise NotImplementedError("Embeddings are not currently supported by Groq API")

ai_service = AIService() 
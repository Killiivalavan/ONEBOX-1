from fastapi import FastAPI
import logging

# Configure basic logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('test_server.log')
    ]
)

logger = logging.getLogger(__name__)

app = FastAPI()

@app.get("/health")
async def health_check():
    logger.info("Health check endpoint called")
    return {"status": "healthy"} 
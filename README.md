# ReachInbox

A modern email management system built with FastAPI and React, featuring AI-powered email categorization and smart inbox management.

## Project Status

### Backend Implementation

- [x] FastAPI server setup with CORS and middleware
- [x] Database initialization and models
- [x] Email service integration
- [x] Account management endpoints
- [x] Health check endpoint
- [x] Error handling and logging
- [x] Environment-based configuration
- [x] AI-powered email classification (using Groq API)
- [x] AI-powered reply generation
- [x] Rate limiting for AI endpoints
- [ ] Email content processing
- [ ] Full text search implementation
- [ ] Text embeddings for similarity search

### AI Features

- [x] Email classification into categories:
  - Interested
  - Meeting Booked
  - Not Interested
  - Spam
  - Out of Office
- [x] Smart reply generation with context
- [x] Rate limiting and error handling for AI services
- [x] Integration with Groq LLM API
- [ ] Custom embeddings for search
- [ ] Automated email summarization
- [ ] Sentiment analysis

### Frontend Implementation

- [x] React application setup with TypeScript
- [x] Material-UI integration
- [x] API client with retry logic
- [x] Environment configuration
- [x] Account management components
- [x] Email list view
- [ ] Email detail view
- [ ] Search functionality
- [ ] Real-time updates
- [ ] Offline support

### DevOps & Infrastructure

- [x] Development environment setup
- [x] Production configuration
- [x] Error logging and monitoring
- [x] API request queuing
- [ ] CI/CD pipeline
- [ ] Docker containerization
- [ ] Production deployment
- [ ] Automated testing

## Tech Stack

### Backend

- FastAPI (Python 3.8+)
- SQLite Database
- Elasticsearch (for search)
- Pydantic for data validation
- AIOHTTP for async operations

### Frontend

- React 18
- TypeScript 4
- Material-UI v5
- Axios for API calls
- React Query for data fetching
- React Virtualized for performance

## Setup Instructions

### Prerequisites

- Python 3.8 or higher
- Node.js 16 or higher
- npm or yarn
- Elasticsearch (optional)

### Backend Setup

1. Clone the repository

```bash
git clone https://github.com/Killiivalavan/ONEBOX-1.git
cd ONEBOX-1
```

2. Create and activate virtual environment

```bash
python -m venv venv
source venv/bin/activate  # On Windows: .\venv\Scripts\activate
```

3. Install dependencies

```bash
pip install -r requirements.txt
```

4. Set up environment variables

```bash
cp .env.example .env
# Edit .env with your configuration
```

5. Run the server

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### Frontend Setup

1. Navigate to frontend directory

```bash
cd frontend
```

2. Install dependencies

```bash
npm install
# or
yarn install
```

3. Start development server

```bash
npm run start:dev
# or
yarn start:dev
```

## Environment Configuration

### Backend (.env)

- `DISABLE_SEARCH`: Toggle search functionality
- `DISABLE_EMAIL_SERVICE`: Toggle email service
- `DATABASE_URL`: SQLite database URL
- `ELASTICSEARCH_HOST`: Elasticsearch connection URL
- `LOG_LEVEL`: Logging verbosity

### Frontend (.env.development, .env.production)

- `PORT`: Frontend server port (8001)
- `REACT_APP_API_BASE_URL`: Backend API URL
- `REACT_APP_API_TIMEOUT`: API request timeout
- `REACT_APP_RETRY_COUNT`: Failed request retry attempts

## API Endpoints

### Core Endpoints

- `GET /health`: Service health check
- `GET /accounts`: List configured email accounts
- `POST /accounts`: Add new email account
- `DELETE /accounts/{email}`: Remove email account
- `GET /email/recent`: Get recent emails
- `GET /email/search`: Search emails
- `GET /email/{message_id}/content`: Get email content

### Management Endpoints

- `POST /clear-cache`: Clear cached data
- `POST /reindex`: Rebuild search index
- `GET /tasks/{task_id}`: Check background task status

## Error Handling

The application implements comprehensive error handling:

- Request timeout handling
- API retry logic with exponential backoff
- Detailed error logging
- User-friendly error messages
- Request queuing for rate limiting

## Development Guidelines

### Code Style

- Python: PEP 8 standards
- TypeScript: ESLint with Airbnb config
- Commit messages: Conventional Commits format

### Testing

- Backend: pytest for unit tests
- Frontend: Jest and React Testing Library
- API: Postman collection available

### Logging

- Backend: Python logging with rotating file handler
- Frontend: Console logging in development
- Production: Structured logging for monitoring

## License

MIT License - See LICENSE file for details

## Contributing

1. Fork the repository
2. Create your feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

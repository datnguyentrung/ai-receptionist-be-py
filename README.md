---
title: ai-receptionist-be
emoji: 🤖
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---

# AI Receptionist Backend
Dự án quản lý điểm danh bằng AI cho trung tâm Taekwondo Văn Quán.

## Overview

AI-powered attendance management system for Taekwondo Văn Quán center. The backend uses facial recognition to automatically identify students and track attendance in real-time.

## Tech Stack

- **Framework:** FastAPI
- **Database:** PostgreSQL
- **Face Recognition:** InsightFace
- **ORM:** SQLAlchemy
- **Containerization:** Docker

## Project Structure

```
ai-receptionist-be-py/
├── app/
│   ├── api/            # API routes
│   ├── core/           # Configuration & settings
│   ├── models/         # Database models
│   ├── schemas/        # Pydantic schemas
│   ├── services/       # Business logic
│   └── utils/          # Utilities
├── Dockerfile
├── requirements.txt
└── docker-compose.yml
```

## Getting Started

### Prerequisites

- Python 3.10+
- Docker & Docker Compose
- PostgreSQL

### Environment Variables

Create a `.env` file based on `.env.example`:

```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/ai_receptionist
SERVER_URL=http://localhost:8000
```

### Run with Docker

```bash
docker-compose up --build
```

### Run Locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## API Documentation

Once the server is running, access the interactive docs:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## License

This project is proprietary and confidential.

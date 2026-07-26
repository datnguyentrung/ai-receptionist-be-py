---
title: ai-receptionist-be
emoji: 🤖
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---

# AI Receptionist Face Embedding Backend

FastAPI service dedicated to generating a face embedding vector from one
uploaded image. The service does not manage Person records, search a database,
store embeddings, or run check-in business logic.

## Tech Stack

- FastAPI
- InsightFace
- ONNX Runtime
- OpenCV
- NumPy

Telegram/Gemini utility code is kept temporarily, but the Person/database flow
has been removed.

## Environment Variables

Create a `.env` file from `.env.example` when running locally. Do not commit
real secrets.

```env
PROJECT_NAME=AI Receptionist Face Embedding API
INSIGHTFACE_MODEL_NAME=buffalo_l
INSIGHTFACE_PROVIDERS=CPUExecutionProvider
FACE_EMBEDDING_DIMENSION=512
MAX_IMAGE_SIZE_BYTES=5242880
ALLOWED_IMAGE_TYPES=image/jpeg,image/png
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
SERVER_PUBLIC_URL=
GEMINI_API_KEY=
```

Removed environment variables:

- `DATABASE_URL`
- `FACE_MATCH_THRESHOLD`
- `JWT_BASE64_SECRET`

## Run Locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## API Contract

### Generate Face Embedding

```http
POST /face-embeddings
Content-Type: multipart/form-data
```

Form field:

- `file`: image upload containing exactly one face

Success response:

```json
{
  "success": true,
  "embedding": [0.0123, -0.0456, 0.0789],
  "dimension": 512,
  "model": "buffalo_l",
  "errorCode": null,
  "message": null
}
```

Error response:

```json
{
  "success": false,
  "embedding": null,
  "dimension": null,
  "model": "buffalo_l",
  "errorCode": "FACE_NOT_DETECTED",
  "message": "Không phát hiện được khuôn mặt trong ảnh"
}
```

Status mapping:

- `400`: `INVALID_IMAGE_FILE`, `EMPTY_IMAGE_FILE`,
  `UNSUPPORTED_IMAGE_TYPE`, `IMAGE_DECODE_FAILED`
- `413`: `FILE_TOO_LARGE`
- `422`: `FACE_NOT_DETECTED`, `MULTIPLE_FACES_DETECTED`,
  `FACE_EMBEDDING_FAILED`, `INVALID_EMBEDDING`
- `503`: `MODEL_NOT_INITIALIZED`
- `500`: `INTERNAL_ERROR`

Example:

```bash
curl -X POST "http://localhost:8000/face-embeddings" \
  -F "file=@tests/dat.jpg;type=image/jpeg"
```

## Health

```http
GET /health
```

Returns model readiness and service metadata. It no longer checks a database
connection.

## Tests

```bash
python -m pytest
```

The tests mock InsightFace for deterministic validation of image/file errors,
face-count errors, embedding validation, and API response contracts.

## Removed Dependencies

- `sqlalchemy[asyncio]`
- `asyncpg`
- `pgvector`

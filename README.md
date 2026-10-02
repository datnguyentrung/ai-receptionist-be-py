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

## Taekwondo ingestion with ADK Web

The ingestion UI is a separate process and does not add routes or lifecycle
dependencies to the existing API on port `8000`.

```powershell
python -m pip install -r requirements-adk.txt
python -m app.adk_web
```

ADK Web listens on `http://127.0.0.1:8001` by default. Its liveness and
readiness endpoints are `/health/live` and `/health/ready`. Startup is
fail-fast unless PostgreSQL has exactly one `ACTIVE` ontology version and
Neo4j is reachable.

Configure at least `GOOGLE_API_KEY` (or the project's existing Google auth),
the `DB_*` variables, and `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`.
Optional settings include `ADK_WEB_PORT`, `ADK_ALLOWED_ORIGINS`,
`INGESTION_MAX_FILE_SIZE_BYTES`, `INGESTION_CHUNK_SIZE_CHARS`, and
`INGESTION_BATCH_SIZE`.

PostgreSQL configuration is read exclusively from `DB_HOST`, `DB_PORT`,
`DB_NAME`, `DB_USER`, and `DB_PASSWORD`. `DATABASE_URL` is deliberately ignored.
The runtime constructs the asyncpg URL with SQLAlchemy's structured URL API, so
reserved characters in a password are handled safely.

GraphRAG settings have these defaults:

```env
RAG_EMBEDDING_MODEL=gemini-embedding-001
RAG_EMBEDDING_DIMENSION=768
RAG_MIN_COSINE_SCORE=0.55
RAG_DEFAULT_TOP_K=10
RAG_CANDIDATE_LIMIT=20
RAG_CONTEXT_MAX_CHARS=24000
```

For a new database:

```powershell
python -m alembic upgrade head
```

For a database that already contains all seven ontology tables, first verify
that its schema matches the SQLAlchemy ontology models, then run:

```powershell
python -m alembic stamp 0001_ontology_baseline
python -m alembic upgrade head
```

Migrations are never run from application lifespan. Uploaded artifacts and ADK
sessions are local development data; resumable ingestion state is persisted in
PostgreSQL and Neo4j. `finalize_ingestion` only prepares a readiness fingerprint;
graph promotion requires an explicit `fill_ingestion` call.

The ingestion skill is the only owner of the begin/batch/scope/submit/finalize/
fill workflow. Its tools compose repository, ontology, graph, and embedding
primitives directly; there is no Python ingestion orchestrator. Each submitted
batch persists its own ontology `scope_key`.

On fill, the system writes source chunks, ordered chunk links, entity mentions,
normalized knowledge facts, evidence links, and 768-dimensional Gemini
embeddings to Neo4j. It also creates idempotent chunk/fact vector indexes and
chunk/entity full-text indexes. Existing committed workspaces can be indexed
without re-extracting semantic facts:

```powershell
python -m app.commands.backfill_graphrag
```

The separate `graph-qa` skill calls
`retrieve_taekwondo_knowledge(question, scope_key=None, top_k=10)`. Retrieval
combines chunk vector search, chunk full-text search, entity graph expansion up
to two hops, and fact vector search with reciprocal-rank fusion and cosine
reranking. Answers must cite returned passages. When evidence is absent or the
best cosine score is below the configured threshold, the skill returns the
fixed Vietnamese abstention response instead of guessing.

The 30-case evaluation set is in
`docs/evaluation/taekwondo-rag-eval.json`. After capturing retrieval/answer
outputs in a JSON object keyed by case ID, evaluate the release gates with:

```powershell
python -m app.commands.evaluate_graphrag path\to\results.json
```

The command reports Recall@10, MRR, citation correctness, grounded-claim
faithfulness, and abstention accuracy.

The tests mock InsightFace for deterministic validation of image/file errors,
face-count errors, embedding validation, and API response contracts.

The standalone ADK dependency set is maintained in `requirements-adk.txt` and
includes SQLAlchemy, asyncpg, Neo4j, Alembic, Google ADK, and Google GenAI.

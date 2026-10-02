from urllib.parse import unquote, urlsplit

from app.core.config import Settings


def test_db_fields_are_the_only_postgres_source_and_password_is_encoded() -> None:
    settings = Settings(
        _env_file=None,
        DB_HOST="db.example.test",
        DB_PORT=5432,
        DB_NAME="taekwondo",
        DB_USER="postgres.tenant",
        DB_PASSWORD="p@ss:/?#[]",
        DATABASE_URL="postgresql://wrong:wrong@wrong.invalid/wrong",
    )

    parsed = urlsplit(settings.async_database_url)
    assert parsed.hostname == "db.example.test"
    assert parsed.username == "postgres.tenant"
    assert unquote(parsed.password or "") == "p@ss:/?#[]"
    assert parsed.path == "/taekwondo"


def test_graphrag_defaults_are_safe() -> None:
    settings = Settings(_env_file=None)
    assert settings.RAG_EMBEDDING_MODEL == "gemini-embedding-001"
    assert settings.RAG_EMBEDDING_DIMENSION == 768
    assert 0 < settings.RAG_MIN_COSINE_SCORE < 1

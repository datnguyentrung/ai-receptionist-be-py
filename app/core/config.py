import json
import secrets
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Receptionist Face Embedding API"
    INSIGHTFACE_MODEL_NAME: str = "buffalo_l"
    FACE_EXECUTION_PROVIDER: str = "auto"
    FACE_EMBEDDING_DIMENSION: int = 512
    MAX_IMAGE_SIZE_BYTES: int = 5 * 1024 * 1024
    ALLOWED_IMAGE_TYPES: str = "image/jpeg,image/png"
    GEMINI_API_KEY: str = Field(
        default="",
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    )
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    SERVER_PUBLIC_URL: str = ""

    # Standalone ADK Web / ingestion runtime
    GOOGLE_ADK_HIGH_MODEL: str = "gemini-3.5-flash-lite"
    GOOGLE_ADK_LOW_MODEL: str = "gemini-3.1-flash-lite"
    GOOGLE_ADK_FALLBACK: str | None = None
    ADK_WEB_HOST: str = "127.0.0.1"
    ADK_WEB_PORT: int = 8001
    ADK_ALLOWED_ORIGINS: str = "http://localhost:8001,http://127.0.0.1:8001"
    ADK_SESSION_DB_URI: str = "sqlite:///./.adk/sessions.db"
    INGESTION_MAX_FILE_SIZE_BYTES: int = 20 * 1024 * 1024
    INGESTION_CHUNK_SIZE_CHARS: int = 6000
    INGESTION_BATCH_SIZE: int = 5
    INGESTION_SHUTDOWN_TIMEOUT_SECONDS: float = 15.0

    # GraphRAG retrieval/indexing
    RAG_EMBEDDING_MODEL: str = "gemini-embedding-001"
    RAG_EMBEDDING_DIMENSION: int = 768
    RAG_EMBEDDING_BATCH_SIZE: int = 32
    RAG_EMBEDDING_MIN_REQUEST_INTERVAL_SECONDS: float = 0.0
    RAG_MIN_COSINE_SCORE: float = 0.55
    RAG_DEFAULT_TOP_K: int = 10
    RAG_CANDIDATE_LIMIT: int = 40
    RAG_CONTEXT_MAX_CHARS: int = 24000
    RAG_RETRIEVAL_TIMEOUT_SECONDS: float = 12.0
    RAG_PATH_TIMEOUT_SECONDS: float = 5.0

    NEO4J_URI: str = ""
    NEO4J_USERNAME: str = ""
    NEO4J_PASSWORD: str = ""
    NEO4J_DATABASE: str = "neo4j"

    # Database Configuration (PostgreSQL / Supabase)
    DB_HOST: str = "aws-0-ap-southeast-1.pooler.supabase.com"
    DB_PORT: int = 5432
    DB_USER: str = "postgres.enjbieisclwmyrgyskha"
    DB_PASSWORD: str = ""
    DB_NAME: str = "postgres"
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10

    # Ingestion
    INGESTION_MAX_BATCH_CHUNKS: int = 5
    INGESTION_MAX_BATCH_CHARS: int = 15000
    INGESTION_TRUE_CHUNK_CACHE: bool = False
    INGESTION_ESTIMATED_CHARS_PER_TOKEN: float = 2.0
    INGESTION_MAX_BATCH_ESTIMATED_TOKENS: int = 15000
    INGESTION_SEMANTIC_EVIDENCE_MODE: str = "on"
    INGESTION_SEMANTIC_EVIDENCE_CANARY_PERCENT: int = 100
    INGESTION_SEMANTIC_EVIDENCE_MAX_CALLS_PER_BATCH: int = 1
    INGESTION_EVIDENCE_TARGETED_REPAIR_MAX_CALLS: int = 1
    # Semantic Evidence Gate (rules + NLI + LLM fallback): off | shadow | enforce
    INGESTION_SEMANTIC_GATE_MODE: str = "shadow"
    INGESTION_SEMANTIC_GATE_BLOCK_UNCERTAIN: bool = True
    INGESTION_NLI_MODEL: str = "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
    INGESTION_NLI_DEVICE: str = "auto"  # auto = GPU nếu có, fallback CPU
    INGESTION_NLI_BATCH_SIZE: int = 16
    INGESTION_NLI_CACHE_SIZE: int = 4096
    INGESTION_NLI_SUPPORT_THRESHOLD: float = 0.90
    INGESTION_NLI_REFUTE_THRESHOLD: float = 0.90
    INGESTION_NLI_MINIMUM_MARGIN: float = 0.20

    # Clef
    CLOUDFLARE_AUTH_TOKEN: str = ""
    CLOUDFLARE_ACCOUNT_ID: str = ""

    # MQTT
    MQTT_TCP_HOST: str
    MQTT_TCP_PORT: int
    MQTT_WS_HOST: str
    MQTT_WS_PORT: int
    MQTT_INTERNAL_HOST: str
    MQTT_INTERNAL_PORT: int
    ESP32_INPUT_TOPIC: str
    DEVICE_HEARTBEAT_TOPIC: str = "taekwondo/devices/+/heartbeat"
    MOBILE_OUTPUT_TOPIC: str
    MQTT_QOS: int
    SENSOR_KIOSK_MAP: str = "{}"
    MOBILE_COMMAND_TOPIC_TEMPLATE: str = "taekwondo/kiosks/{deviceId}/commands"
    MOTION_DEBOUNCE_SECONDS: float = 5.0
    ESP32_HEARTBEAT_TIMEOUT_SECONDS: float = 90.0
    FOG_GATEWAY_DEVICE_CODE: str = ""
    FOG_GATEWAY_DEVICE_SECRET: str = ""
    FOG_GATEWAY_CREDENTIAL_PATH: str = ".fog/gateway-credential.json"
    FOG_RELAY_USERNAME: str = "__fog_relay__"
    FOG_RELAY_SECRET: str = ""
    FOG_REGISTRY_CACHE_PATH: str = ".fog/device-registry.json"
    FOG_SYNC_INTERVAL_SECONDS: float = 30.0

    # Java Cloud face check-in integration
    JAVA_CLOUD_BASE_URL: str = "http://127.0.0.1:8080"
    JAVA_FOG_SECRET: str = ""
    # JWT of a read-only Java service account used by ADK catalogue FunctionTools.
    JAVA_AGENT_API_TOKEN: str = ""
    JAVA_REQUEST_TIMEOUT_SECONDS: float = 10.0
    FOG_INTERNAL_BASE_URL: str = "http://127.0.0.1:8000"
    FOG_INTERNAL_SECRET: str = ""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    @property
    def async_database_url(self) -> str:
        """Build the PostgreSQL DSN exclusively from the DB_* settings.

        URL.create handles reserved characters in credentials without leaking
        precedence to an unrelated DATABASE_URL environment variable.
        """

        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.DB_USER,
            password=self.DB_PASSWORD,
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.DB_NAME,
        ).render_as_string(hide_password=False)

    @property
    def face_execution_provider(self) -> str:
        provider = self.FACE_EXECUTION_PROVIDER.strip().lower()
        if provider not in {"auto", "cpu", "cuda"}:
            raise ValueError(
                "FACE_EXECUTION_PROVIDER must be one of: auto, cpu, cuda"
            )
        return provider

    @property
    def allowed_image_types(self) -> set[str]:
        return {
            content_type.strip().lower()
            for content_type in self.ALLOWED_IMAGE_TYPES.split(",")
            if content_type.strip()
        }

    @property
    def adk_allowed_origins(self) -> list[str]:
        return [
            value.strip()
            for value in self.ADK_ALLOWED_ORIGINS.split(",")
            if value.strip()
        ]

    @property
    def internal_broker_url(self) -> str:
        return f"mqtt://{self.MQTT_INTERNAL_HOST}:{self.MQTT_INTERNAL_PORT}"

    @property
    def sensor_kiosk_map(self) -> dict[str, str]:
        try:
            value = json.loads(self.SENSOR_KIOSK_MAP)
        except json.JSONDecodeError as exc:
            raise ValueError("SENSOR_KIOSK_MAP must be a JSON object") from exc
        if not isinstance(value, dict):
            raise TypeError("SENSOR_KIOSK_MAP must be a JSON object")
        mapping = {
            str(sensor_id).strip(): str(kiosk_id).strip()
            for sensor_id, kiosk_id in value.items()
            if str(sensor_id).strip() and str(kiosk_id).strip()
        }
        return mapping

    def mobile_command_topic(self, device_id: str) -> str:
        if "{deviceId}" not in self.MOBILE_COMMAND_TOPIC_TEMPLATE:
            raise ValueError("MOBILE_COMMAND_TOPIC_TEMPLATE must contain {deviceId}")
        return self.MOBILE_COMMAND_TOPIC_TEMPLATE.format(deviceId=device_id)

    def ensure_relay_credential(self) -> str:
        """Create a process-local credential used only by the embedded relay."""
        if not self.FOG_RELAY_SECRET:
            self.FOG_RELAY_SECRET = secrets.token_urlsafe(32)
        return self.FOG_RELAY_SECRET

    @property
    def relay_broker_url(self) -> str:
        return (
            f"mqtt://{self.FOG_RELAY_USERNAME}:{self.FOG_RELAY_SECRET}"
            f"@{self.MQTT_INTERNAL_HOST}:{self.MQTT_INTERNAL_PORT}"
        )

    def load_fog_gateway_credential(self) -> bool:
        """Hydrate the enrolled Fog credential without putting its secret in .env."""
        path = Path(self.FOG_GATEWAY_CREDENTIAL_PATH)
        if not path.is_absolute():
            path = BASE_DIR / path
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            device_code = str(payload["deviceCode"]).strip()
            secret = str(payload["secret"]).strip()
            java_url = str(payload["javaUrl"]).strip()
        except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError):
            return False
        if not device_code or not secret or not java_url:
            return False
        self.FOG_GATEWAY_DEVICE_CODE = device_code
        self.FOG_GATEWAY_DEVICE_SECRET = secret
        self.JAVA_CLOUD_BASE_URL = java_url
        return True


settings = Settings()

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_cloud_project: str = ""
    google_cloud_location: str = "us-central1"
    google_genai_use_vertexai: bool = True
    llm_model: str = "gemini-2.5-flash"
    embedding_model: str = "text-embedding-005"
    embedding_dim: int = 768

    database_url: str = "postgresql+asyncpg://jeevanreddy@localhost:5432/ir_agent"

    ir_backend_url: str = "http://localhost:9000"
    ir_backend_token: str = "dev-token"

    app_env: str = "local"
    log_level: str = "INFO"


settings = Settings()

# Google ADK builds its own genai client from the process environment, not from this object.
# Export the Vertex settings so ADK uses Vertex AI (ADC) instead of looking for an API key.
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE" if settings.google_genai_use_vertexai else "FALSE")
if settings.google_cloud_project:
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.google_cloud_project)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", settings.google_cloud_location)

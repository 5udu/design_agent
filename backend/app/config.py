from pathlib import Path
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.domain.models import ReasoningEffort

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')
    deepseek_api_key: str = Field(default='', repr=False)
    deepseek_base_url: str = 'https://api.deepseek.com'
    deepseek_model: str = 'deepseek-flash'
    deepseek_reasoning_effort: ReasoningEffort = 'max'
    seedream_api_key: str = Field(default='', repr=False)
    seedream_base_url: str = 'https://ark.cn-beijing.volces.com/api/v3'
    seedream_model: str = 'doubao-seedream-5-0-flash-260915'
    max_model_steps: int = Field(default=16, ge=1)
    max_image_calls: int = 3
    context_window_tokens: int = Field(default=1_000_000, ge=128)
    context_output_reserve: int = Field(default=384_000, ge=0)
    context_safety_tokens: int = Field(default=16_384, ge=0)
    context_compact_ratio: float = Field(default=0.8, gt=0, lt=1)
    context_retain_tokens: int = Field(default=32_768, ge=1)
    context_summary_max_tokens: int = Field(default=4096, ge=128)
    ocr_url: str = 'http://127.0.0.1:8766'
    data_dir: Path = ROOT / 'data'
    database_url: str = Field(default='', repr=False)
    db_pool_size: int = Field(default=20, ge=4, le=100)
    session_ttl_seconds: int = Field(default=28800, ge=300, le=604800)
    cookie_secure: bool = False
    app_origin: str = 'http://127.0.0.1:8000'
    sse_poll_seconds: float = Field(default=2, ge=0.1, le=30)
    worker_concurrency: int = Field(default=3, ge=1, le=20)
    global_concurrency: int = Field(default=5, ge=1, le=100)
    user_concurrency: int = Field(default=2, ge=1, le=20)
    worker_lease_seconds: int = Field(default=60, ge=30, le=600)
    worker_shutdown_seconds: int = Field(default=45, ge=1, le=600)
    deployment_mode: str = 'development'

    @model_validator(mode='after')
    def production_requirements(self):
        if self.db_pool_size < self.worker_concurrency+4:
            raise ValueError('DB_POOL_SIZE must be at least WORKER_CONCURRENCY + 4')
        if self.deployment_mode not in {'development', 'production'}:
            raise ValueError('DEPLOYMENT_MODE must be development or production')
        if self.deployment_mode == 'production' and (not self.database_url.startswith(('postgresql://', 'postgres://')) or not self.cookie_secure or not self.app_origin.startswith('https://')):
            raise ValueError('Production requires PostgreSQL, HTTPS APP_ORIGIN and COOKIE_SECURE=true')
        return self

    def safe_error(self, error: object) -> str:
        value = str(error)
        for key in (self.deepseek_api_key, self.seedream_api_key, self.database_url):
            if key:
                value = value.replace(key, '[redacted]')
        return value[:1200]


settings = Settings()

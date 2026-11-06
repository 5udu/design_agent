from pathlib import Path
from pydantic import Field
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

    def safe_error(self, error: object) -> str:
        value = str(error)
        for key in (self.deepseek_api_key, self.seedream_api_key):
            if key:
                value = value.replace(key, '[redacted]')
        return value[:1200]


settings = Settings()

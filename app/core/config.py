import logging
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    PROJECT_NAME: str = "Quote Inquiry Service"

    # SQL Server 数据库配置
    DB_SERVER: str = "192.168.0.9"
    DB_NAME: str = "HL_QS"
    DB_USER: str = "EG264021"
    DB_PASSWORD: str = "clj264021."
    DATABASE_URL: str = ""

    # DeepSeek (via Volcengine ARK)
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://ark.cn-beijing.volces.com/api/v3"
    DEEPSEEK_MODEL: str = "ep-20260527115853-rw5f9"

    # JWT
    JWT_SECRET: str = "change-me-in-production"
    JWT_EXPIRE_HOURS: int = 72

    # LLM 最大并发数
    LLM_MAX_CONCURRENCY: int = 5

    # 审价科账号（仍使用 BPM / 本地用户表进行密码认证）
    REVIEWER_USERNAMES: str = "EG253100,EG199214"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def sqlalchemy_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"mssql+pyodbc://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_SERVER}/{self.DB_NAME}"
            "?driver=ODBC+Driver+17+for+SQL+Server&TrustServerCertificate=yes"
        )

    def check_jwt_secret(self):
        if self.JWT_SECRET == "change-me-in-production":
            logger.warning(
                "\n  ⚠ JWT_SECRET 仍为默认值！请在 .env 中设置 JWT_SECRET=<随机密钥>\n"
                "  生成方式: python -c \"import secrets; print(secrets.token_hex(32))\""
            )

    @property
    def reviewer_usernames(self) -> set[str]:
        return {name.strip().upper() for name in self.REVIEWER_USERNAMES.split(",") if name.strip()}


settings = Settings()
settings.check_jwt_secret()

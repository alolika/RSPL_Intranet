from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    db_server: str
    db_port: int = 1433
    db_name: str
    db_user: str
    db_password: str
    db_driver: str = "{ODBC Driver 17 for SQL Server}"

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    cors_origins: str = "http://localhost:4200"

    # Public Angular app URL used to build action links in outbound emails
    # (e.g. the HOD's leave-sanction link) — must be reachable by whoever
    # receives the email, not this API's own host.
    frontend_base_url: str = "https://rsplintranet.apps.retailware.in"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()

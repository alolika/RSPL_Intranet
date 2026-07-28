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

    # Microsoft Graph app-only credentials for reading Schedule.xlsx from
    # Amol's OneDrive (Executive Schedule / GSchedule, see support_schedule.py).
    # Optional so the app still starts cleanly in environments where this
    # integration isn't configured yet.
    graph_client_id: str = ""
    graph_tenant_id: str = ""
    graph_client_secret: str = ""
    graph_schedule_share_url: str = ""

    # Shared secret Power Automate's HTTP action must send as X-Import-Key
    # when pushing a parsed Schedule.xlsx to POST /support/executive-schedule/import.
    schedule_import_api_key: str = ""

    # Public Angular app URL used to build action links in outbound emails
    # (e.g. the HOD's leave-sanction link) — must be reachable by whoever
    # receives the email, not this API's own host.
    frontend_base_url: str = "https://rsplintranet.apps.retailware.in"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()

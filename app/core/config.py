import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[2]
DATABASE_URL = os.getenv("ZENGENAI_DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError("Set ZENGENAI_DATABASE_URL in your backend .env file.")
JWT_SECRET = os.getenv("ZENGENAI_JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ZENGENAI_ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
MAX_UPLOAD_BYTES = int(os.getenv("ZENGENAI_MAX_UPLOAD_MB", "20")) * 1024 * 1024
UPLOAD_DIR = Path(os.getenv("ZENGENAI_UPLOAD_DIR", str(BASE_DIR / ".data" / "uploads"))).resolve()
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".csv", ".xlsx"}
CLAMAV_HOST = os.getenv("ZENGENAI_CLAMAV_HOST", "").strip()
CLAMAV_PORT = int(os.getenv("ZENGENAI_CLAMAV_PORT", "3310"))
REQUIRE_MALWARE_SCAN = os.getenv("ZENGENAI_REQUIRE_MALWARE_SCAN", "false").strip().lower() == "true"
SMTP_HOST = os.getenv("ZENGENAI_SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("ZENGENAI_SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("ZENGENAI_SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.getenv("ZENGENAI_SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.getenv("ZENGENAI_SMTP_FROM_EMAIL", SMTP_USERNAME).strip()
SMTP_USE_SSL = os.getenv("ZENGENAI_SMTP_USE_SSL", "false").strip().lower() == "true"
FRONTEND_URL = os.getenv(
    "ZENGENAI_FRONTEND_URL", "http://localhost:5173/Ai-workforce-workspace"
).strip().rstrip("/")
LOCAL_LLM_ENABLED = os.getenv("ZENGENAI_LOCAL_LLM_ENABLED", "true").strip().lower() == "true"
LOCAL_LLM_URL = os.getenv("ZENGENAI_LOCAL_LLM_URL", "http://127.0.0.1:11434").strip().rstrip("/")
LOCAL_LLM_MODEL = os.getenv("ZENGENAI_LOCAL_LLM_MODEL", "qwen2.5:1.5b").strip()
LOCAL_LLM_TIMEOUT_SECONDS = float(os.getenv("ZENGENAI_LOCAL_LLM_TIMEOUT_SECONDS", "12"))


def get_allowed_origins() -> list[str]:
    raw_origins = os.getenv(
        "ZENGENAI_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]


def get_jwt_secret() -> str:
    if len(JWT_SECRET) < 32 or JWT_SECRET == "replace-with-a-long-random-secret":
        raise RuntimeError("Set ZENGENAI_JWT_SECRET to a random secret of at least 32 characters.")
    return JWT_SECRET

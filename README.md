# ZengenAI Backend

FastAPI service for ZengenAI. The API is versioned under `/api/v1`; `/health` remains available for deployment health checks.

## Local setup (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit the private `.env` file:

- Set `ZENGENAI_DATABASE_URL` with your PostgreSQL host, actual port, database name, app role, and rotated role password. URL-encode reserved characters in the password.
- Generate a JWT key with `py -c "import secrets; print(secrets.token_urlsafe(48))"` and replace `ZENGENAI_JWT_SECRET`.

Then start the API:

```powershell
fastapi dev app/main.py
```

Open `http://127.0.0.1:8000/docs` for the API reference. On first startup, SQLAlchemy creates the initial tables in the configured PostgreSQL database. Uploaded files are stored under `.data/uploads/`, which is excluded from Git. User passwords are hashed with Argon2, and protected routes require a bearer token.

## Initial endpoints

- `GET /api/v1/health` and `GET /health`
- `POST /api/v1/auth/register` and `POST /api/v1/auth/login`
- `GET /api/v1/finance/workspace` and `PUT /api/v1/finance/workspace`
- `GET /api/v1/finance/documents` and `POST /api/v1/finance/documents`
- `GET /api/v1/finance/documents/{id}/download` and `DELETE /api/v1/finance/documents/{id}`
- `POST /api/v1/finance/documents/{id}/process` extracts review suggestions from text-based invoice PDFs
- `GET /api/v1/finance/records` and `PATCH /api/v1/finance/records/{id}/confirm`

Set `ZENGENAI_ALLOWED_ORIGINS` to a comma-separated list of trusted frontend origins when deploying. Set a strong unique JWT secret and a production database/upload configuration in the deployment environment. Do not commit credentials or API keys.


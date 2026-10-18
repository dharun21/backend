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

During local frontend development, Vite proxies `/api` to this server. Set `VITE_API_BASE_URL` to the deployed API root ending in `/api/v1` for production builds. The browser uses bearer tokens from the API and the backend enforces ownership at the finance workspace level.

Open `http://127.0.0.1:8000/docs` for the API reference. On first startup, SQLAlchemy creates the initial tables in the configured PostgreSQL database. Uploaded files are stored under `.data/uploads/`, which is excluded from Git. User passwords are hashed with Argon2, and protected routes require a bearer token.

Uploads are size-limited, assigned server-generated storage names, checked against their file signatures, and served as private attachments. ClamAV scanning is optional for local development. For production, run a ClamAV daemon, set `ZENGENAI_CLAMAV_HOST` and `ZENGENAI_CLAMAV_PORT`, and set `ZENGENAI_REQUIRE_MALWARE_SCAN=true`; uploads fail closed if the scanner cannot be reached. Signature checks are not a substitute for malware scanning.

## Initial endpoints

- `GET /api/v1/health` and `GET /health`
- `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, and `PATCH /api/v1/auth/me`
- `GET /api/v1/finance/workspace` and `PUT /api/v1/finance/workspace`
- `GET /api/v1/finance/documents` and `POST /api/v1/finance/documents`
- `GET /api/v1/finance/documents/{id}`
- `GET /api/v1/finance/documents/{id}/download` and `DELETE /api/v1/finance/documents/{id}`
- `POST /api/v1/finance/documents/{id}/process` extracts review suggestions from text-based invoice PDFs
- `GET /api/v1/finance/records`, `GET /api/v1/finance/records/{id}`, and `PATCH /api/v1/finance/records/{id}/confirm`

Set `ZENGENAI_ALLOWED_ORIGINS` to a comma-separated list of trusted frontend origins when deploying. Set a strong unique JWT secret and a production database/upload configuration in the deployment environment. Do not commit credentials or API keys.


## Email and in-app notifications

The app stores user-specific in-app notifications in PostgreSQL. Registration creates a welcome notification; finance documents that need review create action notifications. Users can read or mark notifications as read from the bell menu.

Welcome email uses the Python standard library and optional SMTP settings. To send at no provider charge for low-volume development, configure a Gmail account with 2-Step Verification and a Google App Password in the private `.env` using the `ZENGENAI_SMTP_*` settings shown in `.env.example`. Do not use your normal Google password or commit `.env`. Gmail has sending limits and does not guarantee transactional delivery; email failure does not block registration. Keep SMTP settings empty to run without email. Restart FastAPI after changing `.env`.

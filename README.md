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
- `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `POST /api/v1/auth/forgot-password`, `POST /api/v1/auth/reset-password`, and `PATCH /api/v1/auth/me`
- `GET /api/v1/finance/workspace` and `PUT /api/v1/finance/workspace`
- `GET /api/v1/finance/documents` and `POST /api/v1/finance/documents`
- `GET /api/v1/finance/documents/{id}`
- `GET /api/v1/finance/documents/{id}/download` and `DELETE /api/v1/finance/documents/{id}`
- `POST /api/v1/finance/documents/{id}/process` extracts review suggestions from text-based invoice PDFs
- `GET /api/v1/finance/records`, `GET /api/v1/finance/records/{id}`, and `PATCH /api/v1/finance/records/{id}/confirm`

## Local Finance Analyst chat

Chat does not use an external AI provider or paid API key. It combines three local parts:

1. A small Multinomial Naive Bayes intent classifier, trained on synthetic finance questions in app/services/finance_intent_model.py.
2. Python finance tools that calculate amounts and due lists from the signed-in user's **confirmed** records. Those calculations remain the source of truth.
3. An optional Ollama-hosted language model, running on this computer, which interprets open-ended questions and explains the verified answer and relevant workspace context in natural language. Its adapter only permits loopback addresses, so finance data is not sent to a hosted model API. If Ollama is unavailable, the existing finance tools answer instead.

### Enable local conversational AI (Windows)

Install Ollama for Windows, then in PowerShell run:

```powershell
ollama pull qwen2.5:1.5b
ollama serve
```

Ollama may already run in the background after installation. Keep it running while using Finance Analyst. The backend defaults to http://127.0.0.1:11434 and model qwen2.5:1.5b; these can be changed with the ZENGENAI_LOCAL_LLM_* settings in .env. Restart FastAPI after changing those settings. The chat header reports whether a local language model answered or the deterministic finance tools were used.

The Ollama model is an existing open model running locally; ZengenAI does not train that base language model from scratch. The finance intent classifier is our small task-specific model, but it is not a general language model and is not trained on customer records. Training a useful language model from scratch is not practical on an i3 PC. Chat history is stored per account in PostgreSQL, finance queries are workspace-scoped, and document IDs are checked against the signed-in user's workspace. Document text is treated as untrusted input. Unconfirmed extractions are suggestions, not verified totals.

Ollama/model inference can be slow on low-end hardware. The default 1.5B model is a modest local option; response quality and speed depend on available memory and CPU. Production users need a server that runs the model, configured as a private service; do not point this local-only adapter at a public model endpoint.

The training script prints accuracy on the same synthetic examples used for training. That number is only a smoke indicator, not an independent benchmark; add reviewed examples and evaluate on a separate held-out set before relying on it for broader production usage.

Set `ZENGENAI_ALLOWED_ORIGINS` to a comma-separated list of trusted frontend origins when deploying. Set a strong unique JWT secret and a production database/upload configuration in the deployment environment. Do not commit credentials or API keys.

## Deploying alongside the GitHub Pages frontend

GitHub Pages only serves the frontend. Deploy this FastAPI app separately to a backend host and use a managed PostgreSQL database reachable from that host. Configure the backend host's start command as `uvicorn app.main:app --host 0.0.0.0 --port $PORT` (or the host's documented equivalent), with `backend` as the working directory. Set the environment variables from `.env.example` in the backend host's private environment settings; at minimum configure `ZENGENAI_DATABASE_URL`, a fresh `ZENGENAI_JWT_SECRET`, and `ZENGENAI_ALLOWED_ORIGINS` to the frontend origin, for example `https://dharun21.github.io` (origin only, no repository path). Set `ZENGENAI_FRONTEND_URL` to the full Pages URL including `/Ai-workforce-workspace` for password-reset links.

Set the GitHub repository variable `VITE_API_BASE_URL` to the deployed backend URL ending in `/api/v1`, for example `https://api.example.com/api/v1`. The Pages workflow injects this value when it builds the frontend. Since Vite embeds it into public JavaScript, this URL is public configuration; never put secrets or database credentials in it. Push a new commit or rerun the Pages workflow after setting the variable.

Do not use the development `.data/uploads` directory as production storage unless the backend host provides a persistent disk and backups. For multiple instances or ephemeral filesystems, configure shared object storage before enabling user uploads. Use HTTPS for the API and frontend, and verify `/health` is reachable after deployment.


## Email and in-app notifications

The app stores user-specific in-app notifications in PostgreSQL. Registration creates a welcome notification; finance documents that need review create action notifications. Users can read or mark notifications as read from the bell menu.

Welcome email uses the Python standard library and optional SMTP settings. To send at no provider charge for low-volume development, configure a Gmail account with 2-Step Verification and a Google App Password in the private `.env` using the `ZENGENAI_SMTP_*` settings shown in `.env.example`. Do not use your normal Google password or commit `.env`. Gmail has sending limits and does not guarantee transactional delivery; email failure does not block registration. Keep SMTP settings empty to run without email. Restart FastAPI after changing `.env`.

Password recovery emails contain a single-use reset link and a six-digit code. Both expire after 20 minutes; code verification is limited to five attempts and reset requests are throttled per account. Set `ZENGENAI_FRONTEND_URL` to the deployed frontend origin plus its base path (for GitHub Pages, include `/Ai-workforce-workspace`) so reset links return to the correct app. SMTP must be configured for delivery.


## AI access and admin controls

New users have no AI specialist access by default. Specialist access is stored per user in the specialist_access table; the frontend hides disabled specialists and protected finance API routes reject requests without the matching grant. The personal assistant is a separate access switch and can be enabled independently.

After the API has started at least once, make an existing account an administrator from the backend folder. Run:
    .venv\Scripts\python.exe scripts\promote_admin.py

Enter the email for the existing account. Then sign out and sign back in; the account will show an Admin access page with a per-user AI access matrix. Do not promote an account you do not control.

These are manual access grants, not payment verification. Purchase history, automated plan/expiry enforcement, monthly usage counters, pricing controls, ad controls, and a separate admin sign-in page are not implemented yet. The grant table can store an expiry and monthly limit, but request counting does not currently enforce either.

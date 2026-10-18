# ZengenAI Backend

FastAPI service for the ZengenAI application. This starter keeps the first step small: an API app and a health endpoint. Add database, authentication, and specialist modules as the backend requirements are implemented.

## Local setup (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
fastapi dev app/main.py
```

Open `http://127.0.0.1:8000/docs` for interactive API documentation, or `http://127.0.0.1:8000/health` to check the service.

Do not put production credentials or API keys in source control. Keep secrets in environment variables or a deployment secret store.

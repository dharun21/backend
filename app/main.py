from fastapi import FastAPI

app = FastAPI(
    title="ZengenAI API",
    description="Backend API for ZengenAI workspaces and AI specialists.",
    version="0.1.0",
)


@app.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    """Report whether the API process is running."""
    return {"status": "ok"}

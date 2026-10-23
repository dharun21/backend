from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect

from app import models  # noqa: F401 - ensure model metadata is registered
from app.api.v1.router import api_router
from app.core.config import get_allowed_origins, get_jwt_secret
from app.db import Base, engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_jwt_secret()
    Base.metadata.create_all(bind=engine)
    # create_all creates new tables but does not add columns to existing tables.
    # Keep these tiny, idempotent migrations for local databases.
    existing_columns = {column["name"] for column in inspect(engine).get_columns("finance_records")}
    workspace_columns = {column["name"] for column in inspect(engine).get_columns("finance_workspaces")}
    chat_columns = {column["name"] for column in inspect(engine).get_columns("chat_conversations")}
    with engine.begin() as connection:
        if "business_type" not in workspace_columns:
            connection.exec_driver_sql("ALTER TABLE finance_workspaces ADD COLUMN business_type VARCHAR(20)")
        connection.exec_driver_sql(
            "UPDATE finance_workspaces SET business_type = "
            "(SELECT p.business_type FROM business_profiles p WHERE p.user_id = finance_workspaces.owner_id) "
            "WHERE business_type IS NULL AND EXISTS "
            "(SELECT 1 FROM business_profiles p WHERE p.user_id = finance_workspaces.owner_id "
            "AND p.business_type IN ('Retail', 'Corporate'))"
        )
        if "payment_status" not in existing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE finance_records ADD COLUMN payment_status VARCHAR(16) NOT NULL DEFAULT 'unpaid'"
            )
        if "paid_at" not in existing_columns:
            connection.exec_driver_sql("ALTER TABLE finance_records ADD COLUMN paid_at DATE")
        if "amount_paid" not in existing_columns:
            connection.exec_driver_sql("ALTER TABLE finance_records ADD COLUMN amount_paid NUMERIC(14, 2) NOT NULL DEFAULT 0")
            connection.exec_driver_sql(
                "UPDATE finance_records SET amount_paid = total_amount WHERE payment_status = 'paid' AND total_amount IS NOT NULL"
            )
        if "reminder_date" not in existing_columns:
            connection.exec_driver_sql("ALTER TABLE finance_records ADD COLUMN reminder_date DATE")
        if "domain" not in chat_columns:
            connection.exec_driver_sql("ALTER TABLE chat_conversations ADD COLUMN domain VARCHAR(40) NOT NULL DEFAULT 'finance_analyst'")
    yield


app = FastAPI(
    title="ZengenAI API",
    description="Backend API for ZengenAI workspaces and AI specialists.",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["system"], include_in_schema=False)
def legacy_health_check() -> dict[str, str]:
    """Keep the original health URL working during the API transition."""
    return {"status": "ok"}

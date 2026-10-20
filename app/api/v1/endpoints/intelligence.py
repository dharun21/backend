from fastapi import APIRouter

from app.api.dependencies import CurrentUser, Database
from app.schemas.intelligence import FinanceOverviewResponse
from app.services.entitlements import require_specialist_access
from app.services.finance_intelligence import FinanceIntelligence

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


@router.get("/overview", response_model=FinanceOverviewResponse)
def get_overview(user: CurrentUser, db: Database) -> FinanceOverviewResponse:
    require_specialist_access(db, user.id, "finance_analyst")
    fi = FinanceIntelligence(db, user)
    data = fi.compute_overview()
    return FinanceOverviewResponse(**data)


@router.get("/break-even")
def break_even(
    fixed_costs: float,
    price_per_unit: float,
    variable_cost_per_unit: float,
    user: CurrentUser,
    db: Database,
):
    require_specialist_access(db, user.id, "finance_analyst")
    fi = FinanceIntelligence(db, user)
    return fi.break_even(fixed_costs, price_per_unit, variable_cost_per_unit)


@router.get("/gross-margin")
def gross_margin(revenue: float, cogs: float, user: CurrentUser, db: Database):
    require_specialist_access(db, user.id, "finance_analyst")
    fi = FinanceIntelligence(db, user)
    return fi.gross_margin(revenue, cogs)
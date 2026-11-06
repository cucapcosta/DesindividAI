"""
Schemas Pydantic espelhando o contrato OpenAPI em openapi/openapi.yaml.
Fonte da verdade: openapi/openapi.yaml — qualquer mudança lá deve refletir aqui.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ConsentStatus(str, Enum):
    AWAITING_AUTHORISATION = "AWAITING_AUTHORISATION"
    AUTHORISED = "AUTHORISED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"


class ProductType(str, Enum):
    CREDIT_CARD = "CREDIT_CARD"
    PERSONAL_LOAN = "PERSONAL_LOAN"
    PAYROLL_LOAN = "PAYROLL_LOAN"
    OVERDRAFT = "OVERDRAFT"
    VEHICLE_LOAN = "VEHICLE_LOAN"
    MORTGAGE = "MORTGAGE"


class ScenarioId(str, Enum):
    AGGRESSIVE = "aggressive"
    CONSOLIDATION = "consolidation"
    MINIMUM_VIABLE = "minimum_viable"


class LanguageLevel(str, Enum):
    SIMPLE = "simple"
    TECHNICAL = "technical"


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------

class ConsentRequest(BaseModel):
    cpf: str = Field(..., pattern=r"^\d{11}$", json_schema_extra={"example": "01234567890"})
    institutions: List[str] = Field(..., min_length=1, json_schema_extra={"example": ["bb", "itau"]})
    scopes: List[str] = Field(..., min_length=1, json_schema_extra={"example": ["debts:read"]})
    expiration_days: int = Field(default=90, ge=1, le=365)


class ConsentResponse(BaseModel):
    consent_id: UUID
    status: ConsentStatus
    redirect_url: str
    expires_at: str
    created_at: str


class ConsentStatusResponse(BaseModel):
    consent_id: str
    status: ConsentStatus
    institutions: List[str]
    scopes: List[str]
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    authorised_at: Optional[str] = None
    revoked_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Debts
# ---------------------------------------------------------------------------

class Debt(BaseModel):
    debt_id: UUID
    institution: str
    product_type: ProductType
    outstanding_balance: float
    cet_annual: float = Field(..., description="CET anual % a.a. — BCB Resolução 3.517/2007")
    monthly_payment: float
    remaining_installments: int
    priority_rank: int = Field(..., description="1 = maior CET, quitação urgente")


class DebtListResponse(BaseModel):
    pseudonym_id: str
    snapshot_id: UUID
    total_outstanding: float
    debts: List[Debt]
    generated_at: str


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

class SimulationRequest(BaseModel):
    snapshot_id: UUID
    monthly_budget: float = Field(..., gt=0, le=1_000_000)


class MonthlyPaymentItem(BaseModel):
    debt_id: UUID
    amount: float


class MonthPlan(BaseModel):
    month: int
    payments: List[MonthlyPaymentItem]


class SimulationScenario(BaseModel):
    scenario_id: ScenarioId
    label: str
    description: str
    total_months: int
    total_interest_paid: float
    total_savings_vs_minimum: float
    monthly_plan: List[MonthPlan]


class SimulationResponse(BaseModel):
    simulation_id: UUID
    snapshot_id: UUID
    scenarios: List[SimulationScenario] = Field(..., min_length=3, max_length=3)
    generated_at: str


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------

class RecommendationRequest(BaseModel):
    simulation_id: UUID
    language_level: LanguageLevel = LanguageLevel.SIMPLE


class RecommendationResponse(BaseModel):
    recommendation_id: UUID
    recommended_scenario: ScenarioId
    explanation: str = Field(..., description="Texto gerado pelo Granite (LLM)")
    scenarios: List[SimulationScenario]
    generated_at: str
    trace_id: str = Field(..., description="ID de rastreio watsonx.governance — auditoria LGPD")


# ---------------------------------------------------------------------------
# Erro genérico
# ---------------------------------------------------------------------------

class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: Optional[UUID] = None

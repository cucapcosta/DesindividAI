"""
DesendividAÍ — API Gateway
Os Guri da IBM

Inicie com:
    uvicorn src.main:app --reload

Documentação interativa (gerada automaticamente pelo FastAPI):
    http://localhost:8000/docs       ← Swagger UI
    http://localhost:8000/redoc      ← ReDoc
"""

from dotenv import load_dotenv
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

load_dotenv()  # lê .env (na raiz do repo ou no cwd) antes de qualquer módulo consultar o ambiente

from src.api.models import ErrorResponse
from src.api.routers import consent, debts, simulation, recommendation

app = FastAPI(
    title="DesendividAÍ API",
    version="1.0.0",
    description=(
        "Agente de IA que consolida dívidas via Open Finance e Serasa, "
        "prioriza por CET e entrega plano personalizado de quitação."
    ),
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# CORS — espelha a política definida em docs/m1-api-connect-policies.md
# Em produção essas origens são enforçadas pelo IBM API Connect (DataPower).
# Aqui servem para desenvolvimento local.
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://app.bb.com.br",
        "https://portal.bb.com.br",
        "https://sandbox.bb.com.br",
        "http://localhost:3000",   # dev local
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(consent.router)       # M1 — POST /consent, DELETE /consent/{id}
app.include_router(debts.router)         # M2 — GET /debts
app.include_router(simulation.router)    # M3 — POST /simulation
app.include_router(recommendation.router)  # M3 — POST /recommendation

# ---------------------------------------------------------------------------
# Handler global para NotImplementedError
# Retorna 501 enquanto os times não implementam os endpoints
# ---------------------------------------------------------------------------
@app.exception_handler(NotImplementedError)
async def not_implemented_handler(request: Request, exc: NotImplementedError):
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content=ErrorResponse(
            code="NOT_IMPLEMENTED",
            message=f"Endpoint {request.method} {request.url.path} ainda não implementado.",
        ).model_dump(),
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health", tags=["Internal"], include_in_schema=False)
async def health():
    return {"status": "ok"}

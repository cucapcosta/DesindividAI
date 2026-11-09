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
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler as _default_http_exception_handler
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

load_dotenv()  # lê .env (na raiz do repo ou no cwd) antes de qualquer módulo consultar o ambiente

from src.core.log_filters import install_access_log_filter

install_access_log_filter()  # o access log do uvicorn grava a URL; CPF na URL não pode ir para o log

from src.api.models import ErrorResponse
from src.core.errors import ServiceError
from src.api.routers import consent, debts, simulation, recommendation
from src.api.routers import internal

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
app.include_router(consent.router)       # M1 — POST /consent, GET/DELETE /consent/{id}
app.include_router(debts.router)         # M2 — GET /debts
app.include_router(simulation.router)    # M3 — POST /simulation
app.include_router(recommendation.router)  # M3 — POST /recommendation
app.include_router(internal.router)      # M4 — POST /internal/erase, POST /internal/retention/run

# ---------------------------------------------------------------------------
# Handlers de exceção
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """
    Para rotas /consent*: devolve 400 INVALID_REQUEST listando só os nomes dos campos
    inválidos (sem ecoar o valor — LGPD). Para outras rotas mantém o comportamento padrão
    do FastAPI (422 com detalhe completo), preservando o contrato de M2/M3.
    """
    if request.url.path == "/consent" or request.url.path.startswith("/consent/"):
        if any(err.get("type") == "json_invalid" for err in exc.errors()):
            message = "JSON inválido."
        else:
            fields = [".".join(str(loc) for loc in err.get("loc", [])) for err in exc.errors()]
            message = f"Campos inválidos: {', '.join(fields)}"
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(code="INVALID_REQUEST", message=message).model_dump(mode="json"),
        )
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError):
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(code=exc.code, message=exc.message).model_dump(mode="json"),
    )


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
# OpenAPI: /consent* nunca responde 422 (o handler acima converte em 400)
# ---------------------------------------------------------------------------
def _openapi() -> dict:
    if app.openapi_schema is None:
        schema = get_openapi(title=app.title, version=app.version,
                             description=app.description, routes=app.routes)
        for path, path_item in schema["paths"].items():
            if path == "/consent" or path.startswith("/consent/"):
                for operation in path_item.values():
                    operation["responses"].pop("422", None)
        app.openapi_schema = schema
    return app.openapi_schema


app.openapi = _openapi  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Handler para HTTPException com detail dict (usado pelos endpoints /internal/*)
# Converte {"code": ..., "message": ...} para ErrorResponse JSON.
# ---------------------------------------------------------------------------

@app.exception_handler(FastAPIHTTPException)
async def http_exception_handler(request: Request, exc: FastAPIHTTPException):
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        return JSONResponse(
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
            content=ErrorResponse(
                code=exc.detail["code"],
                message=exc.detail.get("message", ""),
            ).model_dump(mode="json"),
        )
    # Fallback: delegar para o handler padrão do FastAPI
    return await _default_http_exception_handler(request, exc)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health", tags=["Internal"], include_in_schema=False)
async def health():
    return {"status": "ok"}

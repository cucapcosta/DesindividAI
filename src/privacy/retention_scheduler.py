"""
Agendamento do job de retenção dentro do processo da API — M4.

O Cloudant não tem TTL, então sem execução periódica do job os dados vencidos
nunca saem. A conta do hackathon não tem Code Engine nem outro agendador
gerenciado, por isso o job roda num laço asyncio iniciado no lifespan da app.

Configuração (lida na subida da app):
    RETENTION_SCHEDULE_ENABLED   "true" liga o agendador (padrão: desligado)
    RETENTION_INTERVAL_MINUTES   intervalo entre execuções (padrão 1440 = 24 h,
                                 mínimo 5)
    RETENTION_INITIAL_DELAY_SECONDS  espera antes da primeira execução
                                 (padrão 60, para a app terminar de subir)
    RETENTION_SCHEDULE_DRY_RUN   "true" só calcula (padrão: aplica)

Várias réplicas da API rodam o job cada uma; é seguro porque run_retention é
idempotente (a segunda execução encontra tudo zerado). Execuções do mesmo
processo nunca se sobrepõem. Em produção, preferir um job agendado único
(ex.: Code Engine cron) chamando `python -m src.privacy.retention` e deixar
RETENTION_SCHEDULE_ENABLED desligado.

Cada execução emite retention.run na trilha (feito pelo próprio run_retention).
Falhas são logadas sem PII e não derrubam o laço.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_MIN_INTERVAL_MINUTES = 5
_run_lock = threading.Lock()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        logger.warning("retention_scheduler: %s inválido — usando %s", name, default)
        return default


@dataclass(frozen=True)
class ScheduleConfig:
    enabled: bool
    interval_seconds: float
    initial_delay_seconds: float
    dry_run: bool

    @classmethod
    def from_env(cls) -> "ScheduleConfig":
        minutes = max(_env_float("RETENTION_INTERVAL_MINUTES", 1440), _MIN_INTERVAL_MINUTES)
        return cls(
            enabled=_env_bool("RETENTION_SCHEDULE_ENABLED"),
            interval_seconds=minutes * 60,
            initial_delay_seconds=max(_env_float("RETENTION_INITIAL_DELAY_SECONDS", 60), 0),
            dry_run=_env_bool("RETENTION_SCHEDULE_DRY_RUN"),
        )


def run_once(dry_run: bool, runner: Optional[Callable[..., object]] = None) -> bool:
    """
    Roda o job uma vez se não houver outra execução em andamento neste processo.
    Retorna True se rodou com sucesso, False se pulou ou falhou.
    """
    if runner is None:
        from src.privacy.retention import run_retention as runner  # type: ignore[assignment]

    if not _run_lock.acquire(blocking=False):
        logger.warning("retention_scheduler: execução anterior ainda em andamento — pulando")
        return False
    try:
        runner(dry_run=dry_run)
        logger.info("retention_scheduler: execução concluída dry_run=%s", dry_run)
        return True
    except Exception as exc:
        logger.error("retention_scheduler: execução falhou exc=%s", type(exc).__name__)
        return False
    finally:
        _run_lock.release()


async def retention_loop(
    config: ScheduleConfig,
    *,
    runner: Optional[Callable[..., object]] = None,
    sleep: Callable[[float], "asyncio.Future"] = asyncio.sleep,
    max_runs: Optional[int] = None,
) -> int:
    """Laço do agendador. max_runs existe para os testes. Retorna quantas rodou."""
    runs = 0
    await sleep(config.initial_delay_seconds)
    while max_runs is None or runs < max_runs:
        await asyncio.to_thread(run_once, config.dry_run, runner)
        runs += 1
        if max_runs is not None and runs >= max_runs:
            break
        await sleep(config.interval_seconds)
    return runs


@asynccontextmanager
async def retention_lifespan(_app):
    """Lifespan do router interno: inicia e cancela o laço conforme o ambiente."""
    config = ScheduleConfig.from_env()
    task: Optional[asyncio.Task] = None
    if config.enabled:
        logger.info(
            "retention_scheduler: ligado intervalo=%ss dry_run=%s",
            int(config.interval_seconds), config.dry_run,
        )
        task = asyncio.create_task(retention_loop(config), name="retention-scheduler")
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

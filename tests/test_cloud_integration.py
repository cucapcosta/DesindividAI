"""
Integração com a cloud real. Pulado por padrão; para rodar (usa .env e escreve no Cloudant):

    RUN_CLOUD_TESTS=1 pytest tests/test_cloud_integration.py -s
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_CLOUD_TESTS") != "1", reason="defina RUN_CLOUD_TESTS=1 para testar contra a cloud real"
)


def test_cloud_smoke():
    import asyncio

    from scripts import cloud_smoke_test as smoke

    smoke.failures = 0
    asyncio.run(smoke.cloud_checks())
    assert smoke.failures == 0
    smoke.granite_check()
    assert smoke.failures == 0
    smoke.api_flow()
    assert smoke.failures == 0

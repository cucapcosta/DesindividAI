import logging

import httpx
import pytest

from src.agents.agent1.sources import (
    HttpSerasaClient,
    InvalidSourceDataError,
    SourceUnavailableError,
    normalize_open_finance_contract,
    normalize_serasa_report,
)
from src.api.models import ProductType


def test_incomplete_serasa_record_raises_invalid_data():
    with pytest.raises(InvalidSourceDataError, match="Serasa"):
        normalize_serasa_report({"negative_records": [{"creditor": "x", "contract": "y"}]})


@pytest.mark.parametrize("payload", [
    {"contractId": "C1"},                                       # sem institution
    {"institution": "bb"},                                      # sem contractId
    {"institution": "bb", "contractId": "C1", "contractDate": "ontem",
     "productSubType": "CREDITO_PESSOAL_SEM_CONSIGNACAO"},      # data inválida
])
def test_incomplete_open_finance_contract_raises_invalid_data(payload):
    with pytest.raises(InvalidSourceDataError, match="Open Finance"):
        normalize_open_finance_contract(payload)


def test_invalid_data_is_a_source_unavailable_error():
    assert issubclass(InvalidSourceDataError, SourceUnavailableError)


def test_unknown_product_is_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="src.agents.agent1.sources"):
        debt = normalize_open_finance_contract(
            {"institution": "bb", "contractId": "C1", "productSubType": "PRODUTO_NOVO"})
        report = normalize_serasa_report({"negative_records": [
            {"creditor": "x", "contract": "y", "amount": "10", "nature": "TELECOM"}]})
    assert debt.product_type == ProductType.PERSONAL_LOAN
    assert report.negative_records[0].product_type == ProductType.PERSONAL_LOAN
    assert "PRODUTO_NOVO" in caplog.text and "TELECOM" in caplog.text


def test_known_product_is_not_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="src.agents.agent1.sources"):
        normalize_open_finance_contract(
            {"institution": "bb", "contractId": "C1", "productSubType": "CHEQUE_ESPECIAL"})
    assert caplog.text == ""


def test_non_json_response_raises_invalid_data(monkeypatch):
    def fake_get(url, **kwargs):
        return httpx.Response(200, text="<html>manutenção</html>", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(InvalidSourceDataError, match="JSON"):
        HttpSerasaClient("https://serasa.example", "k").fetch_report("p")


def test_http_error_raises_source_unavailable(monkeypatch):
    def fake_get(url, **kwargs):
        return httpx.Response(500, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(SourceUnavailableError):
        HttpSerasaClient("https://serasa.example", "k").fetch_report("p")

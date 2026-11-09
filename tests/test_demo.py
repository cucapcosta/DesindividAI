"""Interface de demonstração servida em /demo/ (só fora de produção)."""


def test_demo_page_is_served(client):
    resp = client.get("/demo/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "DesendividAÍ" in resp.text


def test_demo_is_not_in_the_api_contract(client):
    assert not any(path.startswith("/demo") for path in client.app.openapi()["paths"])

from design_audit_agent.web import app


def test_homepage_loads() -> None:
    client = app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert b"Design Audit Agent" in response.data

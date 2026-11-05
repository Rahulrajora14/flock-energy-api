"""[mocked] Service start-up and health endpoints."""


def test_app_starts_and_health_is_healthy(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"status": "healthy", "service": "urja-meter-api", "version": "1.0.0"}


def test_health_does_not_contact_portal(client, portal):
    client.get("/api/health")
    assert sum(portal.calls.values()) == 0


def test_portal_health_ok(client, portal):
    body = client.get("/api/health/portal").json()
    assert body["status"] == "ok"
    assert body["authenticated"] is True
    assert portal.calls["/login"] == 1


def test_portal_health_reports_bad_credentials(client, portal):
    portal.reject_login = True
    body = client.get("/api/health/portal").json()
    assert body["status"] == "unavailable"
    assert body["authenticated"] is False
    assert body["detail"].startswith("portal_authentication_failed")


def test_unknown_route_uses_error_envelope(client):
    resp = client.get("/api/nope")
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "not_found", "message": "Not Found"}}


def test_api_key_enforced_when_configured(make_client):
    secured = make_client(service_api_key="s3cret")
    assert secured.get("/api/health").status_code == 200  # liveness stays open
    denied = secured.get("/api/meters")
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "invalid_api_key"
    assert secured.get("/api/meters", headers={"X-API-Key": "s3cret"}).status_code == 200

import pytest
from django.db import OperationalError

import core.views


@pytest.mark.django_db
def test_health_ok(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


class _BrokenConnection:
    def cursor(self):
        raise OperationalError('connection failed: password="secret-pw"')


def test_health_db_down_hides_error(client, monkeypatch):
    monkeypatch.setattr(core.views, "connection", _BrokenConnection())

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "db": "unavailable"}
    assert "secret-pw" not in response.content.decode()

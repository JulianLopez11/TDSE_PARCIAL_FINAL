from datetime import UTC, datetime, timedelta

from app import CHANGES, app
from fastapi.testclient import TestClient

client = TestClient(app)


def valid_change() -> dict:
    now = datetime.now(UTC)
    return {
        "order_id": "OC-DEMO-001",
        "producer_id": "PROD-001",
        "previous_volume_kg": 500,
        "new_volume_kg": 650,
        "required_delivery_at": (now + timedelta(hours=36)).isoformat(),
        "changed_at": now.isoformat(),
        "reason": "Ajuste de demanda del canal de cafeterias.",
    }


def setup_function() -> None:
    CHANGES.clear()


def test_valid_change_is_traceable_and_urgent() -> None:
    response = client.post("/cambios-pedido", json=valid_change())
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING_PRODUCER_RESPONSE"
    assert body["risk"]["urgent"] is True
    assert body["rag_explanation"]["sources"][0]["id"] == "POL-PROD-01"


def test_invalid_change_without_real_change_returns_conflict() -> None:
    payload = valid_change()
    payload["new_volume_kg"] = payload["previous_volume_kg"]
    assert client.post("/cambios-pedido", json=payload).status_code == 409


def test_duplicate_producer_response_returns_conflict() -> None:
    created = client.post("/cambios-pedido", json=valid_change()).json()
    endpoint = f"/cambios-pedido/{created['change_id']}/respuesta"
    assert (
        client.post(
            endpoint, json={"action": "ACCEPT", "comment": "Podemos atender el cambio."}
        ).status_code
        == 200
    )
    assert (
        client.post(
            endpoint, json={"action": "ACCEPT", "comment": "Respuesta repetida."}
        ).status_code
        == 409
    )

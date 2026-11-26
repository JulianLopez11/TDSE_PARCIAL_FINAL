"""Pruebas reproducibles del prototipo Origen Acuerdos.

Las pruebas desactivan el envío automático para validar el flujo sin enviar
correos reales. Cubren el caso válido, una entrada inválida y un conflicto.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import app as app_module
import pytest
from app import (
    AGENT_TASKS,
    CHANGES,
    COMMITMENTS,
    EMAIL_DRAFTS,
    PROCESSED_INBOUND_MESSAGE_IDS,
    app,
    extract_latest_reply,
    parse_inbound_action,
)
from fastapi.testclient import TestClient

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_demo_state(monkeypatch: pytest.MonkeyPatch):
    """Aísla cada prueba y evita cualquier envío SMTP externo."""
    test_state_file = Path(__file__).with_name(".test-state.json")
    test_state_file.unlink(missing_ok=True)
    monkeypatch.setenv("AGENT_AUTO_SEND_EMAIL", "false")
    monkeypatch.setattr(app_module, "STATE_FILE", test_state_file)
    CHANGES.clear()
    COMMITMENTS.clear()
    AGENT_TASKS.clear()
    EMAIL_DRAFTS.clear()
    PROCESSED_INBOUND_MESSAGE_IDS.clear()
    yield
    test_state_file.unlink(missing_ok=True)


def test_case_state_survives_a_backend_restart() -> None:
    created = client.post("/cambios-pedido", json=valid_change()).json()

    assert app_module.STATE_FILE.exists()
    CHANGES.clear()
    COMMITMENTS.clear()
    AGENT_TASKS.clear()
    EMAIL_DRAFTS.clear()
    app_module.load_persistent_state()

    assert created["change_id"] in CHANGES
    assert created["agent_case"]["draft_id"] in EMAIL_DRAFTS


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


def test_valid_change_creates_traceable_case_and_email_draft() -> None:
    response = client.post("/cambios-pedido", json=valid_change())

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING_PRODUCER_RESPONSE"
    assert body["request_id"]
    assert body["risk"]["urgent"] is True
    assert "POL-PROD-01" in {
        source["id"] for source in body["rag_explanation"]["sources"]
    }
    assert body["agent_case"]["draft_id"] in EMAIL_DRAFTS
    assert EMAIL_DRAFTS[body["agent_case"]["draft_id"]]["status"] == (
        "AWAITING_CONFIRMATION"
    )
    assert EMAIL_DRAFTS[body["agent_case"]["draft_id"]]["message_id"].startswith("<")
    html_body = EMAIL_DRAFTS[body["agent_case"]["draft_id"]]["html_body"]
    assert "Se requiere tu respuesta" in html_body
    assert "Origen Acuerdos" in html_body
    assert "ACEPTO" in html_body


def test_invalid_change_with_negative_volume_returns_validation_error() -> None:
    payload = valid_change()
    payload["new_volume_kg"] = -20

    response = client.post("/cambios-pedido", json=payload)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any(error["loc"][-1] == "new_volume_kg" for error in detail)


def test_duplicate_producer_response_returns_conflict() -> None:
    created = client.post("/cambios-pedido", json=valid_change()).json()
    endpoint = f"/cambios-pedido/{created['change_id']}/respuesta"
    accepted_response = client.post(
        endpoint,
        json={"action": "ACCEPT", "comment": "Podemos atender el cambio."},
    )
    duplicate_response = client.post(
        endpoint,
        json={"action": "ACCEPT", "comment": "Respuesta repetida."},
    )

    assert accepted_response.status_code == 200
    assert duplicate_response.status_code == 409
    assert "No se permiten respuestas duplicadas" in duplicate_response.json()["detail"]


def test_new_event_creates_a_new_version_and_supersedes_the_previous_one() -> None:
    first_payload = valid_change()
    first_payload["source_event_id"] = "ERP-EVENT-0001"
    first = client.post("/cambios-pedido", json=first_payload).json()

    second_payload = valid_change()
    second_payload["source_event_id"] = "ERP-EVENT-0002"
    second_payload["new_volume_kg"] = 675
    second = client.post("/cambios-pedido", json=second_payload).json()

    assert first["commitment_id"] == second["commitment_id"]
    assert first["version"] == 1
    assert second["version"] == 2
    assert CHANGES[first["change_id"]]["status"] == "SUPERSEDED"


def test_repeated_erp_event_returns_the_existing_change() -> None:
    payload = valid_change()
    payload["source_event_id"] = "ERP-EVENT-0001"

    first = client.post("/cambios-pedido", json=payload).json()
    duplicate = client.post("/cambios-pedido", json=payload).json()

    assert first["change_id"] == duplicate["change_id"]
    assert len(CHANGES) == 1


def test_monitor_escalates_a_case_without_producer_response() -> None:
    created = client.post("/cambios-pedido", json=valid_change()).json()
    change = CHANGES[created["change_id"]]
    change["monitoring"]["response_due_at"] = (
        datetime.now(UTC) - timedelta(minutes=1)
    ).isoformat()

    response = client.post("/agent/monitor")

    assert response.status_code == 200
    assert created["change_id"] in response.json()["escalated_change_ids"]
    assert change["status"] == "PENDING_HUMAN_REVIEW"
    assert change["escalation"]["reason"] == "NO_PRODUCER_RESPONSE"


def test_rejected_producer_response_escalates_to_purchases() -> None:
    created = client.post("/cambios-pedido", json=valid_change()).json()
    response = client.post(
        f"/cambios-pedido/{created['change_id']}/respuesta",
        json={"action": "REJECT", "comment": "No podemos atender el cambio."},
    )

    assert response.status_code == 200
    change = CHANGES[created["change_id"]]
    assert change["status"] == "PENDING_HUMAN_REVIEW"
    assert change["escalation"]["reason"] == "PRODUCER_REJECTED"


def test_review_request_explains_the_escalation_to_purchases() -> None:
    created = client.post("/cambios-pedido", json=valid_change()).json()

    response = client.post(
        f"/cambios-pedido/{created['change_id']}/respuesta",
        json={"action": "REVIEW_REQUEST", "comment": "Solicito revisar la fecha."},
    )

    assert response.status_code == 200
    assert "solicitó revisión" in response.json()["next_action"]
    assert CHANGES[created["change_id"]]["escalation"]["reason"] == (
        "REVIEW_REQUESTED"
    )


def test_inbound_reply_parses_only_the_new_producer_decision() -> None:
    reply = (
        "ACEPTO\n\n"
        "El 26 nov. 2026, Origen Acuerdos escribió:\n"
        "> Responde con ACEPTO, RECHAZO o SOLICITO REVISIÓN."
    )

    assert extract_latest_reply(reply).strip() == "ACEPTO"
    assert parse_inbound_action(extract_latest_reply(reply)) == "ACCEPT"
    assert parse_inbound_action("De acuerdo con el cambio.") == "ACCEPT"
    assert parse_inbound_action("Solicito revisar la fecha.") == "REVIEW_REQUEST"

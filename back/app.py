"""API del prototipo Origen Acuerdos.

Simula ERP, trazabilidad, RAG acotado y flujo de aprobacion.
El correo se envía por SMTP real solo tras confirmación y configuración externa.
"""

import os
import re
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Literal
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

load_dotenv()

app = FastAPI(title="Origen Acuerdos API", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

POLICY_SOURCES = [
    {
        "id": "POL-PROD-01",
        "version": "1.0",
        "title": "Aviso y confirmacion de cambios de pedido",
        "rule": "Los cambios urgentes (menos de 48 horas) requieren revision humana.",
    },
    {
        "id": "POL-PROD-02",
        "version": "1.0",
        "title": "Derecho de respuesta del productor",
        "rule": "El productor puede aceptar, rechazar o solicitar revision.",
    },
]

PRODUCERS = {
    "PROD-001": {
        "name": "Finca La Esperanza",
        "email": "julian.lopez-b@mail.escuelaing.edu.co",
    },
    "PROD-002": {
        "name": "Cooperativa Sierra Verde",
        "email": "julian116910@hotmail.com",
    },
}

CHANGES: dict[str, dict] = {}
AGENT_TASKS: list[dict] = []
EMAIL_DRAFTS: dict[str, dict] = {}
URGENT_LEAD_HOURS = 48
HIGH_IMPACT_PERCENTAGE = 25
ERP_ORDERS = [
    {
        "order_id": "OC-DEMO-001",
        "producer_id": "PROD-001",
        "producer_name": "Finca La Esperanza",
        "current_volume_kg": 500,
        "required_delivery_at": "2026-03-28T08:00:00Z",
        "status": "CONFIRMED",
    },
    {
        "order_id": "OC-DEMO-002",
        "producer_id": "PROD-002",
        "producer_name": "Cooperativa Sierra Verde",
        "current_volume_kg": 900,
        "required_delivery_at": "2026-03-30T08:00:00Z",
        "status": "CONFIRMED",
    },
]


class OrderChangeIn(BaseModel):
    order_id: str = Field(min_length=3, max_length=40)
    producer_id: str
    previous_volume_kg: float = Field(gt=0)
    new_volume_kg: float = Field(gt=0)
    required_delivery_at: datetime
    reason: str = Field(min_length=8, max_length=250)
    changed_at: datetime | None = None

    @field_validator("required_delivery_at", "changed_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("La fecha debe incluir zona horaria, por ejemplo Z.")
        return value


class ProducerResponseIn(BaseModel):
    action: Literal["ACCEPT", "REJECT", "REVIEW_REQUEST"]
    comment: str = Field(min_length=3, max_length=500)


class ApprovalIn(BaseModel):
    approved: bool
    approver: str = Field(min_length=3, max_length=100)
    comment: str = Field(min_length=3, max_length=500)


class AgentTaskIn(BaseModel):
    instruction: str = Field(min_length=5, max_length=800)


class EmailUpdateIn(BaseModel):
    recipient: str = Field(min_length=5, max_length=254)
    subject: str = Field(min_length=3, max_length=180)
    body: str = Field(min_length=10, max_length=4000)


class SendEmailIn(BaseModel):
    confirmed: bool
    idempotency_key: str = Field(min_length=8, max_length=100)


def utc_now() -> datetime:
    return datetime.now(UTC)


def get_change_or_404(change_id: str) -> dict:
    if (change := CHANGES.get(change_id)) is None:
        raise HTTPException(status_code=404, detail="Cambio no encontrado.")
    return change


def smtp_status() -> dict:
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM"]
    missing = [key for key in required if not os.getenv(key)]
    return {
        "configured": not missing,
        "authenticated": False,
        "missing": missing,
    }


def get_draft_or_404(draft_id: str) -> dict:
    if (draft := EMAIL_DRAFTS.get(draft_id)) is None:
        raise HTTPException(status_code=404, detail="Borrador de correo no encontrado.")
    return draft


def create_agent_task(status_value: str, instruction: str, **details: object) -> dict:
    task = {
        "task_id": f"TASK-{uuid4().hex[:8].upper()}",
        "status": status_value,
        "instruction": instruction,
        "created_at": utc_now().isoformat(),
        **details,
    }
    AGENT_TASKS.insert(0, task)
    return task


def send_smtp_email(draft: dict) -> None:
    configuration = smtp_status()
    if not configuration["configured"]:
        raise HTTPException(
            status_code=503,
            detail="SMTP no configurado: " + ", ".join(configuration["missing"]),
        )
    message = EmailMessage()
    message["From"] = os.environ["SMTP_FROM"]
    message["To"] = draft["recipient"]
    message["Subject"] = draft["subject"]
    message.set_content(draft["body"])
    use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() == "true"
    smtp_client = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_client(
        os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"]), timeout=20
    ) as client:
        if not use_ssl and os.getenv("SMTP_USE_TLS", "true").lower() == "true":
            client.starttls()
        app_password = "".join(os.environ["SMTP_PASSWORD"].split())
        client.login(os.environ["SMTP_USERNAME"], app_password)
        client.send_message(message)


def create_change_draft(change: dict) -> dict:
    draft_id = f"MAIL-{uuid4().hex[:8].upper()}"
    draft = {
        "draft_id": draft_id,
        "change_id": change["change_id"],
        "recipient": change["notification"]["recipient"],
        "subject": change["notification"]["subject"],
        "body": change["notification"]["body"],
        "status": "AWAITING_CONFIRMATION",
        "created_at": utc_now().isoformat(),
        "send_attempts": 0,
    }
    EMAIL_DRAFTS[draft_id] = draft
    return draft


def orchestrate_change(change: dict) -> dict:
    """Ejecuta el alcance permitido del agente ante un evento del ERP."""
    draft = create_change_draft(change)
    steps = [
        "Evento ERP detectado",
        "Datos obligatorios validados",
        "Políticas aprobadas consultadas",
        "Caso trazable creado",
    ]
    auto_send = os.getenv("AGENT_AUTO_SEND_EMAIL", "false").lower() == "true"
    if auto_send and smtp_status()["configured"]:
        try:
            send_smtp_email(draft)
            draft["status"] = "SENT"
            draft["sent_at"] = utc_now().isoformat()
            steps.append("Correo enviado por SMTP")
            task_status = "NOTIFIED"
            message = "Caso creado y productor notificado."
        except (HTTPException, OSError, smtplib.SMTPException) as error:
            draft["status"] = "FAILED"
            draft["error"] = str(error)
            steps.append("Error de envío registrado")
            task_status = "EMAIL_FAILED"
            message = "Caso creado; el correo falló y requiere reintento."
    else:
        steps.append("Correo preparado para confirmación humana")
        task_status = "AWAITING_CONFIRMATION"
        message = "Caso creado. Revisa y confirma el correo al productor."
    task = create_agent_task(
        task_status,
        f"Procesar cambio {change['change_id']} del ERP",
        change_id=change["change_id"],
        draft_id=draft["draft_id"],
        message=message,
        steps=steps,
    )
    change["agent_case"] = {"task_id": task["task_id"], "draft_id": draft["draft_id"]}
    return task


def build_change_explanation(
    previous_volume_kg: float,
    new_volume_kg: float,
    variation_pct: float,
    lead_hours: float,
) -> str:
    """Crea una explicacion legible para la notificacion y el RAG acotado."""
    volume_message = (
        f"El pedido cambia de {previous_volume_kg:g} kg "
        f"a {new_volume_kg:g} kg ({variation_pct:+g}%)."
    )
    lead_time_message = f"La anticipacion es de {lead_hours:g} horas."
    return f"{volume_message} {lead_time_message}"


def update_simulated_erp(change: dict) -> None:
    """Actualiza solo el ERP ficticio tras la decision correspondiente."""
    order = next(
        (
            item
            for item in ERP_ORDERS
            if item["order_id"] == change["order"]["order_id"]
        ),
        None,
    )
    if order is None:
        raise HTTPException(
            status_code=404, detail="Pedido no encontrado en el ERP simulado."
        )
    order["current_volume_kg"] = change["order"]["new_volume_kg"]
    order["required_delivery_at"] = change["order"]["required_delivery_at"]
    order["status"] = "UPDATED_SIMULATED"
    change["erp_update"] = {
        "status": "SIMULATED_ERP_UPDATE",
        "at": utc_now().isoformat(),
    }
    change["audit_log"].append(
        {
            "event": "ERP_SIMULATED_UPDATED",
            "at": utc_now().isoformat(),
            "actor": "ERP_ADAPTER_SIMULATED",
        }
    )


def register_change(payload: OrderChangeIn) -> dict:
    if payload.producer_id not in PRODUCERS:
        raise HTTPException(
            status_code=404, detail="Productor no registrado para el piloto."
        )
    if payload.previous_volume_kg == payload.new_volume_kg:
        raise HTTPException(
            status_code=409, detail="No hay cambio de volumen que comunicar."
        )
    changed_at = payload.changed_at or utc_now()
    if payload.required_delivery_at <= changed_at:
        raise HTTPException(
            status_code=422,
            detail="La fecha requerida debe ser posterior al momento del cambio.",
        )
    lead_hours = round(
        (payload.required_delivery_at - changed_at).total_seconds() / 3600, 1
    )
    variation_pct = round(
        (payload.new_volume_kg - payload.previous_volume_kg)
        / payload.previous_volume_kg
        * 100,
        1,
    )
    urgent = lead_hours < URGENT_LEAD_HOURS
    high_impact = abs(variation_pct) >= HIGH_IMPACT_PERCENTAGE
    change_id, producer = (
        f"CAM-{uuid4().hex[:8].upper()}",
        PRODUCERS[payload.producer_id],
    )
    explanation = build_change_explanation(
        payload.previous_volume_kg,
        payload.new_volume_kg,
        variation_pct,
        lead_hours,
    )
    notification_body = " ".join(
        [
            f"Hola, {producer['name']}.",
            explanation,
            f"Motivo: {payload.reason}.",
        ]
    )
    change = {
        "change_id": change_id,
        "request_id": str(uuid4()),
        "status": "PENDING_PRODUCER_RESPONSE",
        "created_at": utc_now().isoformat(),
        "order": payload.model_dump(mode="json"),
        "risk": {
            "level": "HIGH" if urgent or high_impact else "NORMAL",
            "lead_hours": lead_hours,
            "volume_variation_pct": variation_pct,
            "urgent": urgent,
            "high_impact": high_impact,
            "requires_human_approval": urgent or high_impact,
        },
        "rag_explanation": {
            "text": explanation,
            "sources": POLICY_SOURCES,
            "limitation": "El RAG explica politicas; no aprueba ni modifica el pedido.",
        },
        "notification": {
            "channel": "SIMULATED_EMAIL",
            "recipient": producer["email"],
            "subject": f"Cambio de pedido {payload.order_id} - {change_id}",
            "body": notification_body,
            "delivery_status": "SIMULATED_NOT_SENT",
        },
        "audit_log": [
            {
                "event": "ORDER_CHANGE_REGISTERED",
                "at": utc_now().isoformat(),
                "actor": "ERP_ADAPTER_SIMULATED",
            }
        ],
    }
    CHANGES[change_id] = change
    orchestrate_change(change)
    return change


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "origen-acuerdos"}


@app.get("/fuentes")
def sources() -> dict:
    return {"sources": POLICY_SOURCES, "scope": "Solo politicas aprobadas del piloto"}


@app.get("/agent/integrations")
def agent_integrations() -> dict:
    smtp = smtp_status()
    return {
        "agent": {"name": "Agente Origen", "status": "AVAILABLE"},
        "tools": [
            {"name": "Consulta de cambio", "available": True},
            {"name": "RAG de políticas", "available": True},
            {
                "name": "SMTP",
                "detail": (
                    "Configurado; autenticación pendiente de verificar."
                    if smtp["configured"]
                    else "Configura SMTP en backend."
                ),
                **smtp,
            },
        ],
    }


@app.get("/agent/activities")
def agent_activities() -> dict:
    return {"activities": AGENT_TASKS[:30], "drafts": list(EMAIL_DRAFTS.values())}


@app.post("/agent/tasks", status_code=status.HTTP_201_CREATED)
def run_agent_task(payload: AgentTaskIn) -> dict:
    instruction = payload.instruction.strip()
    change_match = re.search(r"CAM-[A-Z0-9]{8}", instruction.upper())
    change_id = change_match.group(0) if change_match else None
    lower_instruction = instruction.lower()
    if any(
        word in lower_instruction for word in ("correo", "email", "notifica", "enviar")
    ):
        if not change_id:
            return create_agent_task(
                "NEEDS_INPUT",
                instruction,
                message="Indica el ID CAM-XXXXXXXX para redactar el correo.",
                steps=[],
            )
        change = get_change_or_404(change_id)
        draft_id = f"MAIL-{uuid4().hex[:8].upper()}"
        EMAIL_DRAFTS[draft_id] = {
            "draft_id": draft_id,
            "change_id": change_id,
            "recipient": change["notification"]["recipient"],
            "subject": change["notification"]["subject"],
            "body": change["notification"]["body"],
            "status": "AWAITING_CONFIRMATION",
            "created_at": utc_now().isoformat(),
            "send_attempts": 0,
        }
        return create_agent_task(
            "AWAITING_CONFIRMATION",
            instruction,
            message="Borrador creado. Revísalo y confirma el envío.",
            draft_id=draft_id,
            steps=["Cambio consultado", "Borrador creado"],
        )
    if any(word in lower_instruction for word in ("estado", "trazabilidad", "riesgo")):
        if not change_id:
            return create_agent_task(
                "NEEDS_INPUT",
                instruction,
                message="Indica el ID CAM-XXXXXXXX que deseas consultar.",
                steps=[],
            )
        change = get_change_or_404(change_id)
        return create_agent_task(
            "COMPLETED",
            instruction,
            message=change["rag_explanation"]["text"],
            change_id=change_id,
            steps=["Cambio consultado", "Políticas consultadas"],
        )
    return create_agent_task(
        "NEEDS_INPUT",
        instruction,
        message=(
            "Puedo redactar correos o consultar estado/riesgo. "
            "Incluye un ID CAM-XXXXXXXX."
        ),
        steps=[],
    )


@app.patch("/agent/emails/{draft_id}")
def update_email_draft(draft_id: str, payload: EmailUpdateIn) -> dict:
    draft = get_draft_or_404(draft_id)
    if draft["status"] != "AWAITING_CONFIRMATION":
        raise HTTPException(
            status_code=409, detail="El borrador ya no se puede editar."
        )
    draft.update(payload.model_dump())
    return draft


@app.post("/agent/emails/{draft_id}/send")
def confirm_and_send_email(draft_id: str, payload: SendEmailIn) -> dict:
    draft = get_draft_or_404(draft_id)
    if not payload.confirmed:
        raise HTTPException(
            status_code=422, detail="Confirma el envío antes de continuar."
        )
    if (
        draft["status"] == "SENT"
        or draft.get("idempotency_key") == payload.idempotency_key
    ):
        return draft
    draft["idempotency_key"] = payload.idempotency_key
    draft["status"] = "SENDING"
    draft["send_attempts"] += 1
    try:
        send_smtp_email(draft)
    except (HTTPException, OSError, smtplib.SMTPException) as error:
        draft["status"] = "FAILED"
        draft["error"] = (
            error.detail if isinstance(error, HTTPException) else str(error)
        )
        raise HTTPException(status_code=502, detail=draft["error"]) from error
    draft["status"] = "SENT"
    draft["sent_at"] = utc_now().isoformat()
    return draft


@app.get("/erp/pedidos")
def list_simulated_erp_orders() -> dict:
    return {"system": "ERP_SIMULATED", "orders": ERP_ORDERS}


@app.post("/cambios-pedido", status_code=status.HTTP_201_CREATED)
def register_order_change(payload: OrderChangeIn) -> dict:
    return register_change(payload)


@app.post("/cambios-pedido/{change_id}/respuesta")
def register_producer_response(change_id: str, payload: ProducerResponseIn) -> dict:
    change = get_change_or_404(change_id)
    if "producer_response" in change:
        raise HTTPException(
            status_code=409,
            detail=(
                "El productor ya respondio este cambio. "
                "No se permiten respuestas duplicadas."
            ),
        )
    change["producer_response"] = {
        "action": payload.action,
        "comment": payload.comment,
        "at": utc_now().isoformat(),
    }
    if payload.action == "ACCEPT" and not change["risk"]["requires_human_approval"]:
        change["status"] = "CONFIRMED"
        update_simulated_erp(change)
        next_action = "Cambio confirmado y ERP simulado actualizado."
    elif payload.action == "ACCEPT":
        change["status"], next_action = (
            "PENDING_HUMAN_APPROVAL",
            "Se requiere aprobacion humana antes de actualizar el ERP simulado.",
        )
    else:
        change["status"], next_action = (
            "PENDING_HUMAN_REVIEW",
            "El caso se escalo al responsable de compras.",
        )
    change["audit_log"].append(
        {
            "event": "PRODUCER_RESPONSE_REGISTERED",
            "at": utc_now().isoformat(),
            "actor": "PRODUCER",
        }
    )
    return {
        "change_id": change_id,
        "status": change["status"],
        "next_action": next_action,
    }


@app.post("/cambios-pedido/{change_id}/aprobacion")
def approve_change(change_id: str, payload: ApprovalIn) -> dict:
    change = get_change_or_404(change_id)
    if change["status"] not in {"PENDING_HUMAN_APPROVAL", "PENDING_HUMAN_REVIEW"}:
        raise HTTPException(
            status_code=409,
            detail="El cambio no esta pendiente de una decision humana.",
        )
    change["human_approval"] = payload.model_dump(mode="json") | {
        "at": utc_now().isoformat()
    }
    change["status"] = "CONFIRMED" if payload.approved else "CANCELLED"
    if payload.approved:
        update_simulated_erp(change)
    change["audit_log"].append(
        {
            "event": "HUMAN_DECISION_RECORDED",
            "at": utc_now().isoformat(),
            "actor": payload.approver,
        }
    )
    return {
        "change_id": change_id,
        "status": change["status"],
        "erp_action": "SIMULATED_ERP_UPDATE" if payload.approved else "NO_ERP_UPDATE",
    }


@app.get("/cambios-pedido/{change_id}")
def get_change(change_id: str) -> dict:
    return get_change_or_404(change_id)


@app.get("/dashboard")
def dashboard() -> dict:
    changes = list(CHANGES.values())
    return {
        "total_changes": len(changes),
        "pending_producer_response": sum(
            c["status"] == "PENDING_PRODUCER_RESPONSE" for c in changes
        ),
        "pending_human_review": sum(
            c["status"] in {"PENDING_HUMAN_APPROVAL", "PENDING_HUMAN_REVIEW"}
            for c in changes
        ),
        "confirmed": sum(c["status"] == "CONFIRMED" for c in changes),
        "urgent": sum(c["risk"]["urgent"] for c in changes),
        "changes": changes,
    }


@app.post("/demo/reiniciar")
def reset_demo() -> dict:
    CHANGES.clear()
    AGENT_TASKS.clear()
    EMAIL_DRAFTS.clear()
    return {"status": "DEMO_RESET"}

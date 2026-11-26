"""API del prototipo Origen Acuerdos.

Simula ERP, trazabilidad, RAG acotado y flujo de aprobacion.
El correo se envía por SMTP real solo tras confirmación y configuración externa.
"""

import asyncio
import email
import imaplib
import json
import os
import re
import smtplib
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from email.header import decode_header
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr
from html import escape, unescape
from pathlib import Path
from typing import Literal
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

load_dotenv()

POLICY_SOURCES = [
    {
        "id": "POL-PROD-01",
        "version": "1.0",
        "title": "Aviso y confirmacion de cambios de pedido",
        "rule": "Los cambios urgentes (menos de 48 horas) requieren revision humana.",
        "keywords": ["urgente", "anticipacion", "entrega", "48", "fecha"],
    },
    {
        "id": "POL-PROD-02",
        "version": "1.0",
        "title": "Derecho de respuesta del productor",
        "rule": "El productor puede aceptar, rechazar o solicitar revision.",
        "keywords": ["productor", "respuesta", "aceptar", "rechazar", "revision"],
    },
    {
        "id": "POL-PROD-03",
        "version": "1.0",
        "title": "Datos mínimos para notificar un cambio",
        "rule": (
            "Toda notificacion debe incluir pedido, version, motivo, fecha y nivel "
            "de urgencia."
        ),
        "keywords": ["pedido", "version", "motivo", "notificacion", "volumen"],
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
COMMITMENTS: dict[str, dict] = {}
AGENT_TASKS: list[dict] = []
EMAIL_DRAFTS: dict[str, dict] = {}
PROCESSED_INBOUND_MESSAGE_IDS: set[str] = set()
STATE_FILE = Path(__file__).parent / "data" / "state.json"
URGENT_LEAD_HOURS = 48
HIGH_IMPACT_PERCENTAGE = 25
RESPONSE_SLA_HOURS = 24
# Intervalo corto para demostrar en local que el agente reacciona a un correo.
MONITOR_INTERVAL_SECONDS = int(os.getenv("AGENT_MONITOR_INTERVAL_SECONDS", "15"))
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


def persist_state() -> None:
    """Conserva la evidencia del prototipo entre reinicios locales."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "changes": CHANGES,
        "commitments": COMMITMENTS,
        "agent_tasks": AGENT_TASKS,
        "email_drafts": EMAIL_DRAFTS,
        "processed_inbound_message_ids": sorted(PROCESSED_INBOUND_MESSAGE_IDS),
        "erp_orders": ERP_ORDERS,
    }
    temporary_file = STATE_FILE.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary_file.replace(STATE_FILE)


def load_persistent_state() -> None:
    """Restaura casos, borradores y trazabilidad si ya existen localmente."""
    if not STATE_FILE.exists():
        return
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    CHANGES.update(state.get("changes", {}))
    COMMITMENTS.update(state.get("commitments", {}))
    AGENT_TASKS.extend(state.get("agent_tasks", []))
    EMAIL_DRAFTS.update(state.get("email_drafts", {}))
    PROCESSED_INBOUND_MESSAGE_IDS.update(
        state.get("processed_inbound_message_ids", [])
    )
    if saved_orders := state.get("erp_orders"):
        ERP_ORDERS[:] = saved_orders


async def monitor_loop() -> None:
    while True:
        await asyncio.sleep(MONITOR_INTERVAL_SECONDS)
        monitor_open_cases()
        if inbound_email_enabled():
            with suppress(HTTPException, OSError, imaplib.IMAP4.error):
                await asyncio.to_thread(sync_inbound_responses)


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_persistent_state()
    monitor_task = asyncio.create_task(monitor_loop())
    try:
        yield
    finally:
        monitor_task.cancel()
        with suppress(asyncio.CancelledError):
            await monitor_task


app = FastAPI(title="Origen Acuerdos API", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class OrderChangeIn(BaseModel):
    order_id: str = Field(min_length=3, max_length=40)
    producer_id: str
    source_event_id: str | None = Field(default=None, min_length=8, max_length=100)
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


def get_order_or_404(order_id: str) -> dict:
    order = next((item for item in ERP_ORDERS if item["order_id"] == order_id), None)
    if order is None:
        raise HTTPException(
            status_code=404, detail="Pedido no encontrado en el ERP simulado."
        )
    return order


def find_change_by_source_event(source_event_id: str) -> dict | None:
    return next(
        (
            change
            for change in CHANGES.values()
            if change["source_event_id"] == source_event_id
        ),
        None,
    )


def get_or_create_commitment(order: dict) -> dict:
    commitment = next(
        (
            item
            for item in COMMITMENTS.values()
            if item["order_id"] == order["order_id"]
        ),
        None,
    )
    if commitment is not None:
        return commitment
    commitment = {
        "commitment_id": f"COM-{uuid4().hex[:8].upper()}",
        "order_id": order["order_id"],
        "producer_id": order["producer_id"],
        "producer_name": order["producer_name"],
        "status": "OPEN",
        "current_change_id": None,
        "version_count": 0,
        "change_ids": [],
        "created_at": utc_now().isoformat(),
        "updated_at": utc_now().isoformat(),
    }
    COMMITMENTS[commitment["commitment_id"]] = commitment
    return commitment


def retrieve_relevant_policies(change_text: str) -> list[dict]:
    """Recuperación léxica acotada sobre políticas aprobadas del piloto."""
    query_terms = set(change_text.lower().replace("-", " ").split())
    ranked = sorted(
        POLICY_SOURCES,
        key=lambda source: len(query_terms.intersection(source["keywords"])),
        reverse=True,
    )
    relevant = [
        source for source in ranked if query_terms.intersection(source["keywords"])
    ]
    return relevant or [POLICY_SOURCES[1], POLICY_SOURCES[2]]


def add_audit_event(change: dict, event: str, actor: str, **details: object) -> None:
    change["audit_log"].append(
        {
            "event": event,
            "at": utc_now().isoformat(),
            "actor": actor,
            **details,
        }
    )


def update_commitment_status(change: dict, status_value: str) -> None:
    commitment = COMMITMENTS[change["commitment_id"]]
    commitment["status"] = status_value
    commitment["updated_at"] = utc_now().isoformat()


def update_notification_status(
    change: dict, status_value: str, error: str | None = None
) -> None:
    change["notification"]["delivery_status"] = status_value
    change["notification"]["updated_at"] = utc_now().isoformat()
    if error:
        change["notification"]["error"] = error
    else:
        change["notification"].pop("error", None)


def smtp_status() -> dict:
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM"]
    missing = [key for key in required if not os.getenv(key)]
    return {
        "configured": not missing,
        "authenticated": False,
        "missing": missing,
    }


def inbound_email_enabled() -> bool:
    return os.getenv("AGENT_INBOUND_EMAIL_ENABLED", "false").lower() == "true"


def get_imap_settings() -> dict:
    """Reutiliza las credenciales SMTP cuando el remitente es Gmail."""
    smtp_host = os.getenv("SMTP_HOST", "")
    default_host = (
        smtp_host.replace("smtp.", "imap.", 1)
        if smtp_host.startswith("smtp.")
        else ""
    )
    return {
        "host": os.getenv("IMAP_HOST") or default_host,
        "port": os.getenv("IMAP_PORT", "993"),
        "username": os.getenv("IMAP_USERNAME") or os.getenv("SMTP_USERNAME", ""),
        "password": os.getenv("IMAP_PASSWORD") or os.getenv("SMTP_PASSWORD", ""),
    }


def imap_status() -> dict:
    settings = get_imap_settings()
    missing = [
        key
        for key, value in settings.items()
        if key in {"host", "username", "password"} and not value
    ]
    return {
        "enabled": inbound_email_enabled(),
        "configured": not missing,
        "missing": [f"IMAP_{key.upper()}" for key in missing],
    }


def decode_email_header(value: str | None) -> str:
    """Decodifica encabezados MIME para identificar de forma segura el caso."""
    if not value:
        return ""
    decoded_parts = decode_header(value)
    return "".join(
        part.decode(charset or "utf-8", errors="replace")
        if isinstance(part, bytes)
        else part
        for part, charset in decoded_parts
    )


def extract_email_text(message: email.message.Message) -> str:
    """Obtiene texto legible de correos simples, HTML o multipartes."""
    parts = message.walk() if message.is_multipart() else [message]
    html_content = ""
    for part in parts:
        if "attachment" in part.get("Content-Disposition", ""):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        content = payload.decode(charset, errors="replace")
        if part.get_content_type() == "text/plain":
            return content
        if part.get_content_type() == "text/html":
            html_content = content
    return unescape(re.sub(r"<[^>]+>", " ", html_content))


def extract_latest_reply(text: str) -> str:
    """Descarta el mensaje citado para evaluar únicamente la decisión nueva."""
    reply_markers = ("\non ", "\nel ", "\nde: ", "\nfrom: ")
    lower_text = text.lower()
    cutoff_positions = [
        lower_text.find(marker)
        for marker in reply_markers
        if lower_text.find(marker) != -1
    ]
    latest_text = text[: min(cutoff_positions)] if cutoff_positions else text
    return "\n".join(
        line for line in latest_text.splitlines() if not line.lstrip().startswith(">")
    )


def parse_inbound_action(text: str) -> Literal["ACCEPT", "REJECT", "REVIEW_REQUEST"] | None:
    normalized = " ".join(text.lower().replace("í", "i").split())
    if any(
        term in normalized
        for term in ("solicito revision", "solicitar revision", "revisemos", "revisar")
    ):
        return "REVIEW_REQUEST"
    if any(
        term in normalized
        for term in ("rechazo", "rechazar", "no acepto", "no puedo", "no es posible")
    ):
        return "REJECT"
    if any(
        term in normalized
        for term in ("acepto", "aceptar", "accept", "confirmado", "de acuerdo")
    ):
        return "ACCEPT"
    return None


def get_change_from_email(
    message: email.message.Message, subject: str, body: str
) -> dict | None:
    references = " ".join(
        [message.get("In-Reply-To", ""), message.get("References", "")]
    )
    matching_draft = next(
        (
            draft
            for draft in EMAIL_DRAFTS.values()
            if draft.get("message_id") and draft["message_id"] in references
        ),
        None,
    )
    if matching_draft:
        return CHANGES.get(matching_draft["change_id"])
    email_content = f"{subject}\n{body}".upper()
    return next(
        (
            change
            for change in CHANGES.values()
            if change["change_id"].upper() in email_content
        ),
        None,
    )


def sender_matches_producer(change: dict, sender: str) -> bool:
    sender_address = parseaddr(sender)[1].lower()
    expected_address = change["notification"]["recipient"].lower()
    return sender_address == expected_address


def is_registered_producer_sender(sender: str) -> bool:
    sender_address = parseaddr(sender)[1].lower()
    producer_emails = {producer["email"].lower() for producer in PRODUCERS.values()}
    return sender_address in producer_emails


def sync_inbound_responses() -> dict:
    """Lee respuestas nuevas por IMAP y registra decisiones trazables."""
    configuration = imap_status()
    if not configuration["enabled"]:
        return {"status": "DISABLED", "processed": [], "ignored": []}
    if not configuration["configured"]:
        raise HTTPException(
            status_code=503,
            detail="IMAP no configurado: " + ", ".join(configuration["missing"]),
        )

    processed: list[str] = []
    ignored: list[str] = []
    settings = get_imap_settings()
    with imaplib.IMAP4_SSL(settings["host"], int(settings["port"])) as client:
        app_password = "".join(settings["password"].split())
        client.login(settings["username"], app_password)
        client.select("INBOX")
        # Outlook y Gmail pueden alterar el asunto al responder. Se inspeccionan
        # solo mensajes recientes de productores registrados, sin exponerlos.
        result, data = client.uid("search", None, "ALL")
        if result != "OK":
            raise OSError("No fue posible consultar las respuestas por IMAP.")
        for message_id in data[0].split()[-40:]:
            message_uid = message_id.decode()
            if message_uid in PROCESSED_INBOUND_MESSAGE_IDS:
                continue
            result, header_data = client.uid(
                "fetch", message_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])"
            )
            if result != "OK" or not header_data or not header_data[0]:
                continue
            raw_header = header_data[0][1]
            if not isinstance(raw_header, bytes):
                continue
            header = email.message_from_bytes(raw_header)
            if not is_registered_producer_sender(header.get("From", "")):
                continue
            result, message_data = client.uid("fetch", message_id, "(BODY.PEEK[])")
            if result != "OK" or not message_data or not message_data[0]:
                ignored.append(message_uid)
                continue
            raw_message = message_data[0][1]
            if not isinstance(raw_message, bytes):
                ignored.append(message_uid)
                continue
            message = email.message_from_bytes(raw_message)
            subject = decode_email_header(message.get("Subject"))
            body = extract_latest_reply(extract_email_text(message))
            change = get_change_from_email(message, subject, body)
            action = parse_inbound_action(body)
            if change is None:
                ignored.append(message_uid)
                PROCESSED_INBOUND_MESSAGE_IDS.add(message_uid)
                continue
            if not sender_matches_producer(change, message.get("From", "")):
                ignored.append(message_uid)
                PROCESSED_INBOUND_MESSAGE_IDS.add(message_uid)
                continue
            if "producer_response" in change:
                ignored.append(message_uid)
                PROCESSED_INBOUND_MESSAGE_IDS.add(message_uid)
                continue
            if action is None:
                create_agent_task(
                    "AWAITING_CLARIFICATION",
                    f"Aclarar respuesta del productor para {change['change_id']}",
                    change_id=change["change_id"],
                    message=(
                        "El correo fue asociado al caso, pero no expresa una "
                        "decisión clara. Se requiere aclaración del productor."
                    ),
                    steps=["Hilo identificado", "Respuesta ambigua detectada"],
                )
                add_audit_event(
                    change,
                    "INBOUND_EMAIL_AMBIGUOUS",
                    "AGENT",
                    message_uid=message_uid,
                )
                ignored.append(message_uid)
                PROCESSED_INBOUND_MESSAGE_IDS.add(message_uid)
                continue
            apply_producer_response(
                change,
                action,
                body[:500] or "Respuesta recibida por correo electrónico.",
                actor="PRODUCER_EMAIL",
            )
            PROCESSED_INBOUND_MESSAGE_IDS.add(message_uid)
            client.uid("store", message_id, "+FLAGS", "\\Seen")
            processed.append(change["change_id"])
    if processed or ignored:
        persist_state()
    return {"status": "COMPLETED", "processed": processed, "ignored": ignored}


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
    message["Message-ID"] = draft["message_id"]
    message.set_content(draft["body"])
    message.add_alternative(
        draft.get("html_body") or build_plain_email_html(draft["body"]),
        subtype="html",
    )
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


def build_plain_email_html(body: str) -> str:
    """Mantiene legible un borrador modificado manualmente en clientes HTML."""
    safe_body = escape(body).replace("\n", "<br>")
    return f"""
    <!doctype html>
    <html lang="es">
      <body style="margin:0;background:#f5f7f5;color:#1f2933;font-family:Arial,sans-serif;">
        <main style="max-width:640px;margin:0 auto;padding:32px 16px;">
          <section style="background:#ffffff;border:1px solid #d9e2dc;border-radius:12px;padding:28px;">
            <p style="margin:0;font-size:20px;font-weight:700;color:#123c2a;">Origen Acuerdos</p>
            <p style="margin:20px 0 0;font-size:15px;line-height:1.65;">{safe_body}</p>
          </section>
        </main>
      </body>
    </html>
    """


def build_change_email_html(change: dict) -> str:
    """Genera el correo HTML estructurado sin ocultar información operativa."""
    order = change["order"]
    risk = change["risk"]
    producer_name = escape(change["notification"]["producer_name"])
    reason = escape(order["reason"])
    risk_label = "ALTA PRIORIDAD" if risk["level"] == "HIGH" else "CAMBIO PROGRAMADO"
    risk_color = "#a33a00" if risk["level"] == "HIGH" else "#1f6b4f"
    risk_background = "#fff0e8" if risk["level"] == "HIGH" else "#e9f7ef"
    response_subject = escape(
        f"Respuesta {change['change_id']} - {order['order_id']}"
    )
    return f"""
    <!doctype html>
    <html lang="es">
      <body style="margin:0;background:#f5f7f5;color:#1f2933;font-family:Arial,sans-serif;">
        <main style="max-width:640px;margin:0 auto;padding:32px 16px;">
          <section style="overflow:hidden;background:#ffffff;border:1px solid #d9e2dc;border-radius:14px;">
            <header style="padding:24px 28px;background:#123c2a;color:#ffffff;">
              <p style="margin:0;font-size:18px;font-weight:700;">Origen Acuerdos</p>
              <p style="margin:6px 0 0;font-size:13px;color:#d8eee1;">Notificación de cambio de pedido</p>
            </header>
            <div style="padding:28px;">
              <span style="display:inline-block;padding:6px 10px;border-radius:999px;background:{risk_background};color:{risk_color};font-size:12px;font-weight:700;letter-spacing:.4px;">{risk_label}</span>
              <h1 style="margin:18px 0 10px;font-size:25px;line-height:1.25;color:#123c2a;">Se requiere tu respuesta</h1>
              <p style="margin:0;font-size:16px;line-height:1.6;">Hola, {producer_name}. Registramos un cambio en el pedido <strong>{escape(order['order_id'])}</strong>. Por favor revísalo y responde a este correo.</p>
              <table role="presentation" style="width:100%;margin:24px 0;border-collapse:separate;border-spacing:0;border:1px solid #d9e2dc;border-radius:10px;overflow:hidden;font-size:14px;">
                <tr><td style="padding:12px 14px;background:#f1f6f2;color:#52635a;width:42%;">Cambio</td><td style="padding:12px 14px;font-weight:700;">{escape(change['change_id'])} · versión {change['version']}</td></tr>
                <tr><td style="padding:12px 14px;background:#f1f6f2;color:#52635a;border-top:1px solid #d9e2dc;">Volumen</td><td style="padding:12px 14px;border-top:1px solid #d9e2dc;"><strong>{order['previous_volume_kg']:g} kg → {order['new_volume_kg']:g} kg</strong> ({risk['volume_variation_pct']:+g}%)</td></tr>
                <tr><td style="padding:12px 14px;background:#f1f6f2;color:#52635a;border-top:1px solid #d9e2dc;">Anticipación</td><td style="padding:12px 14px;border-top:1px solid #d9e2dc;">{risk['lead_hours']:g} horas</td></tr>
                <tr><td style="padding:12px 14px;background:#f1f6f2;color:#52635a;border-top:1px solid #d9e2dc;">Motivo</td><td style="padding:12px 14px;border-top:1px solid #d9e2dc;">{reason}</td></tr>
              </table>
              <section style="padding:18px;border-left:4px solid #2f7d57;background:#f6faf7;">
                <p style="margin:0 0 8px;font-size:14px;font-weight:700;color:#123c2a;">¿Cómo respondo?</p>
                <p style="margin:0;font-size:14px;line-height:1.6;">Responde este correo indicando una de estas opciones: <strong>ACEPTO</strong>, <strong>RECHAZO</strong> o <strong>SOLICITO REVISIÓN</strong>. Si es necesario, agrega una observación.</p>
              </section>
              <p style="margin:24px 0 0;font-size:12px;line-height:1.5;color:#65736b;">Caso: {escape(change['change_id'])} · Solicitud: {escape(change['request_id'])}. El agente registra y notifica el cambio; no modifica pedidos, pagos ni contratos sin aprobación humana.</p>
            </div>
          </section>
        </main>
      </body>
    </html>
    """


def create_change_draft(change: dict) -> dict:
    draft_id = f"MAIL-{uuid4().hex[:8].upper()}"
    sender_domain = os.environ.get("SMTP_FROM", "origen.local").split("@")[-1]
    draft = {
        "draft_id": draft_id,
        "change_id": change["change_id"],
        "message_id": make_msgid(domain=sender_domain),
        "recipient": change["notification"]["recipient"],
        "subject": change["notification"]["subject"],
        "body": change["notification"]["body"],
        "html_body": change["notification"]["html_body"],
        "status": "AWAITING_CONFIRMATION",
        "created_at": utc_now().isoformat(),
        "send_attempts": 0,
        "idempotency_key": None,
    }
    EMAIL_DRAFTS[draft_id] = draft
    return draft


def orchestrate_change(change: dict) -> dict:
    """Ejecuta el alcance permitido del agente ante un evento del ERP."""
    draft = create_change_draft(change)
    policy_ids = ", ".join(
        source["id"] for source in change["rag_explanation"]["sources"]
    )
    steps = [
        "Evento ERP detectado",
        "Datos obligatorios validados",
        f"Políticas aprobadas recuperadas: {policy_ids}",
        "Caso trazable creado",
    ]
    auto_send = os.getenv("AGENT_AUTO_SEND_EMAIL", "false").lower() == "true"
    if auto_send and smtp_status()["configured"]:
        try:
            send_smtp_email(draft)
            draft["status"] = "SENT"
            draft["sent_at"] = utc_now().isoformat()
            update_notification_status(change, "SENT")
            add_audit_event(change, "NOTIFICATION_SENT", "AGENT")
            steps.append("Correo enviado por SMTP")
            task_status = "NOTIFIED"
            message = "Caso creado y productor notificado."
        except (HTTPException, OSError, smtplib.SMTPException) as error:
            draft["status"] = "FAILED"
            draft["error"] = (
                error.detail if isinstance(error, HTTPException) else str(error)
            )
            update_notification_status(change, "FAILED", draft["error"])
            add_audit_event(change, "NOTIFICATION_FAILED", "AGENT")
            steps.append("Error de envío registrado")
            task_status = "EMAIL_FAILED"
            message = "Caso creado; el correo falló y requiere reintento."
    else:
        update_notification_status(change, "AWAITING_CONFIRMATION")
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


def escalate_change(change: dict, reason: str, message: str) -> dict:
    if change.get("escalation"):
        return change["escalation"]
    escalation = {
        "reason": reason,
        "owner": "Responsable de compras",
        "at": utc_now().isoformat(),
        "status": "PENDING_HUMAN_REVIEW",
    }
    change["escalation"] = escalation
    change["status"] = "PENDING_HUMAN_REVIEW"
    update_commitment_status(change, "ESCALATED")
    add_audit_event(change, "CASE_ESCALATED", "AGENT", reason=reason)
    create_agent_task(
        "ESCALATED",
        f"Escalar cambio {change['change_id']} a compras",
        change_id=change["change_id"],
        message=message,
        steps=["Caso revisado", "Escalamiento registrado", "Compras asignado"],
    )
    return escalation


def monitor_open_cases(reference_time: datetime | None = None) -> list[str]:
    """Escala automáticamente casos cuyo plazo de respuesta ya venció."""
    now = reference_time or utc_now()
    escalated_ids: list[str] = []
    for change in CHANGES.values():
        deadline_text = change.get("monitoring", {}).get("response_due_at")
        if change["status"] != "PENDING_PRODUCER_RESPONSE" or not deadline_text:
            continue
        if datetime.fromisoformat(deadline_text) <= now:
            escalate_change(
                change,
                "NO_PRODUCER_RESPONSE",
                "El productor no respondió dentro del plazo definido; caso escalado.",
            )
            escalated_ids.append(change["change_id"])
    if escalated_ids:
        persist_state()
    return escalated_ids


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
    update_commitment_status(change, "CONFIRMED")


def register_change(payload: OrderChangeIn) -> dict:
    if payload.producer_id not in PRODUCERS:
        raise HTTPException(
            status_code=404, detail="Productor no registrado para el piloto."
        )
    order = get_order_or_404(payload.order_id)
    if order["producer_id"] != payload.producer_id:
        raise HTTPException(
            status_code=422,
            detail="El productor no corresponde al pedido indicado en el ERP simulado.",
        )
    source_event_id = payload.source_event_id or f"ERP-{uuid4().hex[:12].upper()}"
    if duplicate := find_change_by_source_event(source_event_id):
        return duplicate
    if payload.previous_volume_kg == payload.new_volume_kg:
        raise HTTPException(
            status_code=409, detail="No hay cambio de volumen que comunicar."
        )
    if payload.previous_volume_kg != order["current_volume_kg"]:
        raise HTTPException(
            status_code=409,
            detail=(
                "La versión anterior no coincide con el pedido actual del ERP simulado."
            ),
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
    commitment = get_or_create_commitment(order)
    previous_change_id = commitment["current_change_id"]
    if previous_change_id:
        previous_change = CHANGES.get(previous_change_id)
        if previous_change is None:
            commitment["current_change_id"] = None
            commitment["change_ids"] = []
            commitment["version_count"] = 0
        else:
            if previous_change["status"] in {
                "PENDING_PRODUCER_RESPONSE",
                "PENDING_HUMAN_APPROVAL",
                "PENDING_HUMAN_REVIEW",
            }:
                previous_change["status"] = "SUPERSEDED"
                add_audit_event(
                    previous_change,
                    "CHANGE_SUPERSEDED",
                    "AGENT",
                    replacement_source_event_id=source_event_id,
                )
    change_id = f"CAM-{uuid4().hex[:8].upper()}"
    producer = PRODUCERS[payload.producer_id]
    version = commitment["version_count"] + 1
    explanation = build_change_explanation(
        payload.previous_volume_kg,
        payload.new_volume_kg,
        variation_pct,
        lead_hours,
    )
    policy_query = " ".join(
        [
            payload.reason,
            "urgente" if urgent else "programado",
            "productor respuesta pedido version volumen entrega",
        ]
    )
    relevant_policies = retrieve_relevant_policies(policy_query)
    notification_body = "\n\n".join(
        [
            f"Hola, {producer['name']}:",
            "Se requiere tu respuesta frente a un cambio de pedido.",
            f"Pedido: {payload.order_id}\n"
            f"Cambio: {change_id} · Versión: {version}\n"
            f"Volumen: {payload.previous_volume_kg:g} kg → "
            f"{payload.new_volume_kg:g} kg ({variation_pct:+g}%)\n"
            f"Anticipación: {lead_hours:g} horas\n"
            f"Motivo: {payload.reason}",
            "Responde a este correo con ACEPTO, RECHAZO o SOLICITO REVISIÓN. "
            "Puedes añadir una observación.",
            "Origen Acuerdos registra el cambio y la respuesta; cualquier "
            "modificación definitiva requiere aprobación humana.",
        ]
    )
    response_due_at = min(
        changed_at + timedelta(hours=RESPONSE_SLA_HOURS),
        payload.required_delivery_at,
    )
    change = {
        "change_id": change_id,
        "request_id": str(uuid4()),
        "source_event_id": source_event_id,
        "commitment_id": commitment["commitment_id"],
        "version": version,
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
            "sources": relevant_policies,
            "limitation": "El RAG explica politicas; no aprueba ni modifica el pedido.",
        },
        "notification": {
            "channel": "SIMULATED_EMAIL",
            "recipient": producer["email"],
            "producer_name": producer["name"],
            "subject": (
                f"Acción requerida | Pedido {payload.order_id} | "
                f"Cambio {change_id}"
            ),
            "body": notification_body,
            "delivery_status": "PENDING_AGENT",
        },
        "audit_log": [
            {
                "event": "ORDER_CHANGE_REGISTERED",
                "at": utc_now().isoformat(),
                "actor": "ERP_ADAPTER_SIMULATED",
            }
        ],
        "monitoring": {
            "response_due_at": response_due_at.isoformat(),
            "last_checked_at": changed_at.isoformat(),
        },
    }
    change["notification"]["html_body"] = build_change_email_html(change)
    CHANGES[change_id] = change
    commitment["version_count"] = version
    commitment["current_change_id"] = change_id
    commitment["change_ids"].append(change_id)
    commitment["status"] = "PENDING_PRODUCER_RESPONSE"
    commitment["updated_at"] = utc_now().isoformat()
    orchestrate_change(change)
    persist_state()
    return change


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "origen-acuerdos"}


@app.get("/fuentes")
def sources() -> dict:
    return {"sources": POLICY_SOURCES, "scope": "Solo politicas aprobadas del piloto"}


@app.get("/agent/activities")
def agent_activities() -> dict:
    monitor_open_cases()
    active_drafts = sorted(
        EMAIL_DRAFTS.values(),
        key=lambda draft: draft["created_at"],
        reverse=True,
    )
    return {
        "activities": AGENT_TASKS[:30],
        "drafts": active_drafts,
        "summary": {
            "open_commitments": sum(
                item["status"] not in {"CONFIRMED", "CANCELLED"}
                for item in COMMITMENTS.values()
            ),
            "awaiting_producer": sum(
                change["status"] == "PENDING_PRODUCER_RESPONSE"
                for change in CHANGES.values()
            ),
            "escalated": sum(
                change["status"] == "PENDING_HUMAN_REVIEW"
                for change in CHANGES.values()
            ),
        },
    }


@app.patch("/agent/emails/{draft_id}")
def update_email_draft(draft_id: str, payload: EmailUpdateIn) -> dict:
    draft = get_draft_or_404(draft_id)
    if draft["status"] not in {"AWAITING_CONFIRMATION", "FAILED"}:
        raise HTTPException(
            status_code=409, detail="El borrador ya no se puede editar."
        )
    draft.update(payload.model_dump())
    draft["html_body"] = build_plain_email_html(draft["body"])
    draft["status"] = "AWAITING_CONFIRMATION"
    draft.pop("error", None)
    change = get_change_or_404(draft["change_id"])
    change["notification"].update(
        {
            "recipient": draft["recipient"],
            "subject": draft["subject"],
            "body": draft["body"],
            "html_body": draft["html_body"],
        }
    )
    update_notification_status(change, "AWAITING_CONFIRMATION")
    add_audit_event(change, "NOTIFICATION_DRAFT_UPDATED", "HUMAN")
    persist_state()
    return draft


@app.post("/agent/emails/{draft_id}/send")
def confirm_and_send_email(draft_id: str, payload: SendEmailIn) -> dict:
    draft = get_draft_or_404(draft_id)
    change = get_change_or_404(draft["change_id"])
    if not payload.confirmed:
        raise HTTPException(
            status_code=422, detail="Confirma el envío antes de continuar."
        )
    if (
        draft["status"] == "SENT"
        or draft.get("idempotency_key") == payload.idempotency_key
    ):
        return draft
    if draft["status"] == "SENDING":
        raise HTTPException(status_code=409, detail="El correo ya se está enviando.")
    draft["idempotency_key"] = payload.idempotency_key
    draft["status"] = "SENDING"
    draft["send_attempts"] += 1
    update_notification_status(change, "SENDING")
    try:
        send_smtp_email(draft)
    except (HTTPException, OSError, smtplib.SMTPException) as error:
        draft["status"] = "FAILED"
        draft["error"] = (
            error.detail if isinstance(error, HTTPException) else str(error)
        )
        update_notification_status(change, "FAILED", draft["error"])
        add_audit_event(change, "NOTIFICATION_FAILED", "AGENT")
        persist_state()
        raise HTTPException(status_code=502, detail=draft["error"]) from error
    draft["status"] = "SENT"
    draft["sent_at"] = utc_now().isoformat()
    update_notification_status(change, "SENT")
    add_audit_event(change, "NOTIFICATION_SENT", "HUMAN_CONFIRMED")
    persist_state()
    return draft


@app.post("/agent/monitor")
def run_agent_monitor() -> dict:
    escalated_ids = monitor_open_cases()
    inbox_result = sync_inbound_responses()
    return {
        "status": "MONITORING_COMPLETED",
        "escalated_change_ids": escalated_ids,
        "inbox": inbox_result,
        "checked_at": utc_now().isoformat(),
    }


@app.get("/agent/inbox/status")
def inbound_email_status() -> dict:
    """Expone el estado de recepción sin revelar datos sensibles."""
    return imap_status()


@app.post("/agent/inbox/sync")
def sync_agent_inbox() -> dict:
    """Procesa respuestas recibidas por correo bajo demanda."""
    return sync_inbound_responses()


@app.get("/erp/pedidos")
def list_simulated_erp_orders() -> dict:
    return {"system": "ERP_SIMULATED", "orders": ERP_ORDERS}


@app.post("/cambios-pedido", status_code=status.HTTP_201_CREATED)
def register_order_change(payload: OrderChangeIn) -> dict:
    return register_change(payload)


def apply_producer_response(
    change: dict,
    action: Literal["ACCEPT", "REJECT", "REVIEW_REQUEST"],
    comment: str,
    actor: str = "PRODUCER",
) -> dict:
    """Registra una respuesta sin importar si llegó por portal o por correo."""
    if "producer_response" in change:
        raise HTTPException(
            status_code=409,
            detail=(
                "El productor ya respondio este cambio. "
                "No se permiten respuestas duplicadas."
            ),
        )
    change["producer_response"] = {
        "action": action,
        "comment": comment,
        "at": utc_now().isoformat(),
        "channel": actor,
    }
    if action == "ACCEPT" and not change["risk"]["requires_human_approval"]:
        change["status"] = "CONFIRMED"
        update_simulated_erp(change)
        create_agent_task(
            "COMPLETED",
            f"Cerrar compromiso {change['commitment_id']}",
            change_id=change["change_id"],
            message="El productor aceptó y el ERP simulado fue actualizado.",
            steps=["Respuesta registrada", "ERP actualizado", "Compromiso cerrado"],
        )
        next_action = "Cambio confirmado y ERP simulado actualizado."
    elif action == "ACCEPT":
        change["status"], next_action = (
            "PENDING_HUMAN_APPROVAL",
            "Se requiere aprobacion humana antes de actualizar el ERP simulado.",
        )
        update_commitment_status(change, "PENDING_HUMAN_APPROVAL")
        create_agent_task(
            "AWAITING_HUMAN_APPROVAL",
            f"Solicitar aprobación humana para {change['change_id']}",
            change_id=change["change_id"],
            message="El productor aceptó, pero el cambio requiere aprobación humana.",
            steps=["Respuesta registrada", "Riesgo alto detectado", "Compras asignado"],
        )
    else:
        reason = (
            "PRODUCER_REJECTED" if action == "REJECT" else "REVIEW_REQUESTED"
        )
        escalation_message = (
            "El productor rechazó el cambio; caso escalado a compras."
            if action == "REJECT"
            else "El productor solicitó revisión; caso escalado a compras."
        )
        escalate_change(
            change,
            reason,
            escalation_message,
        )
        next_action = (
            "El productor rechazó el cambio; el caso se escaló a compras."
            if action == "REJECT"
            else "El productor solicitó revisión; el caso se escaló a compras."
        )
    add_audit_event(
        change,
        "PRODUCER_RESPONSE_REGISTERED",
        actor,
        action=action,
    )
    return {
        "change_id": change["change_id"],
        "status": change["status"],
        "next_action": next_action,
    }


@app.post("/cambios-pedido/{change_id}/respuesta")
def register_producer_response(change_id: str, payload: ProducerResponseIn) -> dict:
    change = get_change_or_404(change_id)
    response = apply_producer_response(change, payload.action, payload.comment)
    persist_state()
    return response


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
    else:
        update_commitment_status(change, "CANCELLED")
    add_audit_event(
        change,
        "HUMAN_DECISION_RECORDED",
        payload.approver,
        approved=payload.approved,
    )
    create_agent_task(
        "COMPLETED" if payload.approved else "CANCELLED",
        f"Registrar decisión humana para {change_id}",
        change_id=change_id,
        message=(
            "Cambio aprobado y ERP simulado actualizado."
            if payload.approved
            else "Cambio cancelado por decisión humana."
        ),
        steps=["Decisión humana registrada", "Compromiso actualizado"],
    )
    persist_state()
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
    monitor_open_cases()
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
        "escalated": sum(c["status"] == "PENDING_HUMAN_REVIEW" for c in changes),
        "commitments": list(COMMITMENTS.values()),
        "changes": changes,
    }


@app.post("/demo/reiniciar")
def reset_demo() -> dict:
    CHANGES.clear()
    COMMITMENTS.clear()
    AGENT_TASKS.clear()
    EMAIL_DRAFTS.clear()
    PROCESSED_INBOUND_MESSAGE_IDS.clear()
    persist_state()
    return {"status": "DEMO_RESET"}

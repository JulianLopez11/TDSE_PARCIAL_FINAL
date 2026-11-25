import { Change } from "../services/api";
import { label } from "../utils/formatters";
import { Button } from "./Button";
import { View } from "./Navigation";

export function CaseCard({
  change,
  mode,
  onAction,
}: {
  change: Change;
  mode: View;
  onAction: (id: string, action: string) => void;
}) {
  const producerActions =
    mode === "producer" && change.status === "PENDING_PRODUCER_RESPONSE";
  const controlActions =
    mode === "control" &&
    ["PENDING_HUMAN_APPROVAL", "PENDING_HUMAN_REVIEW"].includes(change.status);
  return (
    <article className={`case ${change.risk.urgent ? "high" : ""}`}>
      <header>
        <div>
          <h3>
            {change.order.order_id} <span>·</span> {change.change_id}
          </h3>
          <p>{change.notification.recipient}</p>
        </div>
        <span className={`status ${change.risk.urgent ? "alert" : ""}`}>
          {label(change.status)}
        </span>
      </header>
      <p>
        <strong>
          {change.order.previous_volume_kg} kg → {change.order.new_volume_kg} kg
        </strong>{" "}
        · {change.risk.volume_variation_pct}% · anticipación: {change.risk.lead_hours} h
      </p>
      <p>{change.rag_explanation.text}</p>
      <p>
        Respuesta del productor:{" "}
        <strong>{change.producer_response?.action ?? "Pendiente"}</strong>
      </p>
      {producerActions && (
        <div className="actions">
          <Button onClick={() => onAction(change.change_id, "ACCEPT")}>Aceptar</Button>
          <Button variant="danger" onClick={() => onAction(change.change_id, "REJECT")}>
            Rechazar
          </Button>
          <Button onClick={() => onAction(change.change_id, "REVIEW_REQUEST")}>
            Pedir revisión
          </Button>
        </div>
      )}
      {controlActions && (
        <div className="actions">
          <Button onClick={() => onAction(change.change_id, "APPROVE")}>
            Aprobar y actualizar ERP
          </Button>
          <Button
            variant="danger"
            onClick={() => onAction(change.change_id, "REJECT_APPROVAL")}
          >
            No aprobar
          </Button>
        </div>
      )}
      <details>
        <summary>Ver evidencia registrada</summary>
        <dl>
          <dt>Request ID</dt>
          <dd>{change.request_id}</dd>
          <dt>RAG acotado</dt>
          <dd>
            {change.rag_explanation.sources.map((source) => source.id).join(", ")}
          </dd>
          <dt>Correo</dt>
          <dd>{change.notification.delivery_status}</dd>
          <dt>Eventos</dt>
          <dd>{change.audit_log.map((event) => event.event).join(" → ")}</dd>
        </dl>
      </details>
    </article>
  );
}

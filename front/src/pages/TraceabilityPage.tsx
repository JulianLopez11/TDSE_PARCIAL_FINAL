import { CaseCard } from "../components/CaseCard";
import { EmptyState } from "../components/EmptyState";
import { Dashboard } from "../services/api";

export function TraceabilityPage({ dashboard }: { dashboard: Dashboard }) {
  const metrics = [
    [dashboard.total_changes, "Cambios"],
    [dashboard.urgent, "Urgentes"],
    [dashboard.pending_producer_response, "Esperando productor"],
    [dashboard.pending_human_review, "Revisión humana"],
    [dashboard.escalated, "Escalados"],
    [dashboard.confirmed, "Confirmados"],
  ];
  return (
    <section>
      <div className="section-heading">
        <div>
          <p className="kicker">Observabilidad</p>
          <h2>Trazabilidad del piloto</h2>
        </div>
      </div>
      <div className="metrics">
        {metrics.map(([value, name]) => (
          <div className="metric" key={String(name)}>
            <strong>{value}</strong>
            <span>{name}</span>
          </div>
        ))}
      </div>
      <div className="stack">
        {dashboard.changes.length ? (
          dashboard.changes.map((change) => (
            <CaseCard
              change={change}
              mode="trace"
              onAction={() => undefined}
              key={change.change_id}
            />
          ))
        ) : (
          <EmptyState
            title="Aún no hay trazas"
            description="El dashboard se poblará al registrar un cambio desde el ERP simulado."
            className="panel"
          />
        )}
      </div>
    </section>
  );
}

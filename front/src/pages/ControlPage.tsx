import { CaseCard } from "../components/CaseCard";
import { EmptyState } from "../components/EmptyState";
import { Change } from "../services/api";

export function ControlPage({
  changes,
  onAction,
}: {
  changes: Change[];
  onAction: (id: string, action: string) => void;
}) {
  const pending = changes.filter((change) =>
    ["PENDING_HUMAN_APPROVAL", "PENDING_HUMAN_REVIEW"].includes(change.status),
  );
  return (
    <section>
      <div className="section-heading">
        <div>
          <p className="kicker">Supervisión humana</p>
          <h2>Casos que requieren decisión</h2>
        </div>
      </div>
      <div className="stack">
        {pending.length ? (
          pending.map((change) => (
            <CaseCard
              change={change}
              mode="control"
              onAction={onAction}
              key={change.change_id}
            />
          ))
        ) : (
          <EmptyState
            title="Sin decisiones pendientes"
            description="Los casos que requieran aprobación humana aparecerán aquí."
            className="panel"
          />
        )}
      </div>
    </section>
  );
}

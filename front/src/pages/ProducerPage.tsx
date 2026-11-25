import { CaseCard } from "../components/CaseCard";
import { EmptyState } from "../components/EmptyState";
import { Change } from "../services/api";

export function ProducerPage({
  changes,
  onAction,
}: {
  changes: Change[];
  onAction: (id: string, action: string) => void;
}) {
  return (
    <section>
      <div className="section-heading">
        <div>
          <p className="kicker">Respuesta externa</p>
          <h2>Bandeja del productor</h2>
        </div>
      </div>
      <div className="stack">
        {changes.length ? (
          changes.map((change) => (
            <CaseCard
              change={change}
              mode="producer"
              onAction={onAction}
              key={change.change_id}
            />
          ))
        ) : (
          <EmptyState
            title="Sin cambios notificados"
            description="Los cambios enviados al productor aparecerán aquí."
            className="panel"
          />
        )}
      </div>
    </section>
  );
}

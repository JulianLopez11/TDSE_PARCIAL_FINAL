import { useEffect, useState } from "react";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { AgentSummary, AgentTask, EmailDraft, origenApi } from "../services/api";
import { dateLocal, label } from "../utils/formatters";

type Props = {
  onNotice: (message: string) => void;
  onError: (message: string) => void;
  onDataChange: () => Promise<void>;
};

const emptySummary: AgentSummary = {
  open_commitments: 0,
  awaiting_producer: 0,
  escalated: 0,
};

export function AgentPage({ onNotice, onError, onDataChange }: Props) {
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [drafts, setDrafts] = useState<EmailDraft[]>([]);
  const [summary, setSummary] = useState<AgentSummary>(emptySummary);
  const [activeDraft, setActiveDraft] = useState<EmailDraft | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const activities = await origenApi.activities();
      setTasks(activities.activities);
      setDrafts(activities.drafts);
      setSummary(activities.summary);
      setActiveDraft(
        (current) =>
          activities.drafts.find((draft) => draft.draft_id === current?.draft_id) ??
          activities.drafts.find((draft) => draft.status !== "SENT") ??
          current,
      );
    } catch (error) {
      onError(error instanceof Error ? error.message : "No se pudo cargar el agente.");
    }
  };

  useEffect(() => {
    void load();
    const refreshId = window.setInterval(() => void load(), 15_000);
    return () => window.clearInterval(refreshId);
  }, []);

  const actionableDrafts = drafts.filter((draft) => draft.status !== "SENT");

  const saveDraft = async () => {
    if (!activeDraft) return;
    setBusy(true);
    try {
      const saved = await origenApi.updateDraft(activeDraft.draft_id, activeDraft);
      setActiveDraft(saved);
      onNotice("Borrador actualizado. Aún no se ha enviado.");
      await load();
    } catch (error) {
      onError(
        error instanceof Error ? error.message : "No se pudo actualizar el borrador.",
      );
    } finally {
      setBusy(false);
    }
  };

  const sendDraft = async () => {
    if (!activeDraft) return;
    setBusy(true);
    try {
      const sent = await origenApi.sendDraft(activeDraft.draft_id);
      setActiveDraft(sent);
      onNotice("Correo confirmado por el servidor SMTP.");
      await load();
    } catch (error) {
      onError(error instanceof Error ? error.message : "El correo no se pudo enviar.");
      await load();
    } finally {
      setBusy(false);
    }
  };

  const syncInbox = async () => {
    setBusy(true);
    try {
      const result = await origenApi.syncInbox();
      const message = result.processed.length
        ? `Se registró la respuesta de ${result.processed.join(", ")}.`
        : "No hay respuestas nuevas para registrar.";
      onNotice(message);
      await onDataChange();
      await load();
    } catch (error) {
      onError(
        error instanceof Error
          ? error.message
          : "No se pudo revisar el correo entrante.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="agent-page" aria-busy={busy}>
      <div className="agent-header">
        <div>
          <h1>Monitor operativo</h1>
          <p>
            El agente monitorea los cambios del ERP y ejecuta el flujo definido. Aquí
            puedes revisar su evidencia y aprobar los correos que lo requieran.
          </p>
        </div>
        <span className={`agent-state ${busy ? "working" : ""}`}>
          {busy ? "Procesando" : "Disponible"}
        </span>
      </div>

      <div className="command-actions agent-inbox-action">
        <Button variant="quiet" type="button" onClick={syncInbox} disabled={busy}>
          Revisar respuestas de correo
        </Button>
        <p>El monitor también revisa el buzón cada minuto cuando IMAP está activo.</p>
      </div>

      <div className="agent-layout">
        <div className="agent-main">
          <section className="agent-protocol" aria-label="Alcance del agente">
            <strong>Flujo automático</strong>
            <span>Detectar cambio</span>
            <span>Validar</span>
            <span>Consultar RAG</span>
            <span>Notificar y escalar</span>
          </section>
          <dl className="agent-status-strip" aria-label="Estado operativo">
            <div>
              <dt>Compromisos abiertos</dt>
              <dd>{summary.open_commitments}</dd>
            </div>
            <div>
              <dt>Esperando productor</dt>
              <dd>{summary.awaiting_producer}</dd>
            </div>
            <div>
              <dt>Escalados a compras</dt>
              <dd>{summary.escalated}</dd>
            </div>
          </dl>
          {activeDraft && (
            <section className="email-workbench" aria-labelledby="email-title">
              <div className="section-heading">
                <div>
                  <h2 id="email-title">Revisar correo</h2>
                </div>
                <span
                  className={`status ${activeDraft.status === "FAILED" ? "alert" : ""}`}
                >
                  {label(activeDraft.status)}
                </span>
              </div>
              <label>
                Para
                <input
                  value={activeDraft.recipient}
                  onChange={(event) =>
                    setActiveDraft({ ...activeDraft, recipient: event.target.value })
                  }
                />
              </label>
              <label>
                Asunto
                <input
                  value={activeDraft.subject}
                  onChange={(event) =>
                    setActiveDraft({ ...activeDraft, subject: event.target.value })
                  }
                />
              </label>
              <label>
                Mensaje
                <textarea
                  value={activeDraft.body}
                  onChange={(event) =>
                    setActiveDraft({ ...activeDraft, body: event.target.value })
                  }
                />
              </label>
              {activeDraft.error && <p className="inline-error">{activeDraft.error}</p>}
              <div className="command-actions">
                <Button
                  variant="quiet"
                  type="button"
                  onClick={saveDraft}
                  disabled={busy}
                >
                  Guardar cambios
                </Button>
                <Button
                  type="button"
                  onClick={sendDraft}
                  disabled={busy || activeDraft.status === "SENT"}
                >
                  {activeDraft.status === "SENT"
                    ? "Enviado"
                    : activeDraft.status === "FAILED"
                      ? "Reintentar envío"
                      : "Confirmar y enviar"}
                </Button>
              </div>
            </section>
          )}

          <section className="activity-feed" aria-labelledby="activity-title">
            <div className="section-heading">
              <div>
                <h2 id="activity-title">Actividad del agente</h2>
              </div>
            </div>
            {tasks.length ? (
              tasks.map((task) => (
                <article className="activity" key={task.task_id}>
                  <div className="activity-meta">
                    <strong>{label(task.status)}</strong>
                    <p>{task.instruction}</p>
                    <small>{dateLocal(task.created_at)}</small>
                  </div>
                  <p className="activity-result">
                    {task.message ?? task.steps.join(" · ")}
                  </p>
                </article>
              ))
            ) : (
              <EmptyState
                title="Aún no hay actividad"
                description="Registra un cambio desde el ERP para iniciar el flujo automático."
              />
            )}
          </section>
        </div>
        <aside className="agent-aside">
          <section>
            <h2>Correos en curso</h2>
            {actionableDrafts.length ? (
              actionableDrafts.map((draft) => (
                <button
                  className="draft-row"
                  key={draft.draft_id}
                  onClick={() => setActiveDraft(draft)}
                >
                  <span>{draft.subject}</span>
                  <small>{label(draft.status)}</small>
                </button>
              ))
            ) : (
              <EmptyState
                title="Sin correos en curso"
                description="El agente mostrará aquí los correos que requieren revisión o reintento."
              />
            )}
          </section>
        </aside>
      </div>
    </section>
  );
}

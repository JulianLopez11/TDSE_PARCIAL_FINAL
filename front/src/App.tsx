import { useState } from "react";
import { Navigation, View } from "./components/Navigation";
import { useOrigenData } from "./hooks/useOrigenData";
import { ControlPage } from "./pages/ControlPage";
import { ErpPage } from "./pages/ErpPage";
import { AgentPage } from "./pages/AgentPage";
import { ProducerPage } from "./pages/ProducerPage";
import { TraceabilityPage } from "./pages/TraceabilityPage";
import { NewChange, origenApi } from "./services/api";

export default function App() {
  const [view, setView] = useState<View>("agent");
  const [notice, setNotice] = useState("");
  const { orders, dashboard, error, setError, refresh } = useOrigenData();
  const show = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(""), 3500);
  };
  const create = async (change: NewChange) => {
    try {
      const result = await origenApi.createChange(change);
      show(`Cambio ${result.change_id} detectado. El agente creó el caso y el correo.`);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Error al crear el cambio.");
    }
  };
  const action = async (id: string, actionType: string) => {
    try {
      if (actionType === "APPROVE" || actionType === "REJECT_APPROVAL")
        await origenApi.approve(id, actionType === "APPROVE");
      else await origenApi.respond(id, actionType);
      show(
        actionType === "APPROVE"
          ? "Decisión registrada y ERP simulado actualizado."
          : "Respuesta registrada y trazada.",
      );
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "No fue posible registrar la acción.",
      );
    }
  };
  const reset = async () => {
    await origenApi.reset();
    show("Demo reiniciada.");
    await refresh();
  };
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span>Origen</span>
        </div>
        <p className="brand-subtitle">Acuerdos de abastecimiento</p>
        <Navigation view={view} onChange={setView} />
        <div className="sidebar-footer">
          <span className="presence-dot" aria-hidden="true" />
          Agente disponible
        </div>
      </aside>
      <main className="workspace">
        <header className="workspace-header">
          <div>
            <p>Operaciones · Café Origen</p>
            <h1>{view === "agent" ? "Agente" : "Compromisos"}</h1>
          </div>
          <span>Entorno local</span>
        </header>
        {error && (
          <div className="message error">
            {error}
            <button onClick={() => setError("")}>Cerrar</button>
          </div>
        )}
        {view === "agent" && <AgentPage onNotice={show} onError={setError} />}
        {view === "erp" && (
          <ErpPage orders={orders} onCreate={create} onReset={reset} />
        )}
        {view === "producer" && (
          <ProducerPage changes={dashboard.changes} onAction={action} />
        )}
        {view === "control" && (
          <ControlPage changes={dashboard.changes} onAction={action} />
        )}
        {view === "trace" && <TraceabilityPage dashboard={dashboard} />}
      </main>
      {notice && (
        <div className="toast" role="status">
          {notice}
        </div>
      )}
    </div>
  );
}

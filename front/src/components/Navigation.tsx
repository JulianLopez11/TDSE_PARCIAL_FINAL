export type View = "agent" | "erp" | "producer" | "control" | "trace";
const items: [View, string][] = [
  ["agent", "Agente"],
  ["erp", "ERP simulado"],
  ["producer", "Portal del productor"],
  ["control", "Control de compras"],
  ["trace", "Trazabilidad"],
];

export function Navigation({
  view,
  onChange,
}: {
  view: View;
  onChange: (view: View) => void;
}) {
  return (
    <nav className="tabs" aria-label="Vistas del prototipo">
      {items.map(([id, text]) => (
        <button
          className={view === id ? "active" : ""}
          onClick={() => onChange(id)}
          key={id}
        >
          {text}
        </button>
      ))}
    </nav>
  );
}

import { FormEvent, useMemo, useState } from "react";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { dateLocal, label } from "../utils/formatters";
import { NewChange, Order } from "../services/api";

export function ErpPage({
  orders,
  onCreate,
  onReset,
}: {
  orders: Order[];
  onCreate: (change: NewChange) => Promise<void>;
  onReset: () => Promise<void>;
}) {
  const [selected, setSelected] = useState("");
  const [volume, setVolume] = useState("650");
  const [reason, setReason] = useState("Ajuste de demanda del canal de cafeterías.");
  const [delivery, setDelivery] = useState(() =>
    new Date(Date.now() + 36 * 3600_000).toISOString().slice(0, 16),
  );
  const selectedOrder = useMemo(
    () => orders.find((order) => order.order_id === (selected || orders[0]?.order_id)),
    [orders, selected],
  );
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedOrder) return;
    await onCreate({
      order_id: selectedOrder.order_id,
      producer_id: selectedOrder.producer_id,
      source_event_id: crypto.randomUUID(),
      previous_volume_kg: selectedOrder.current_volume_kg,
      new_volume_kg: Number(volume),
      required_delivery_at: new Date(delivery).toISOString(),
      changed_at: new Date().toISOString(),
      reason,
    });
  };
  return (
    <section>
      <div className="section-heading">
        <div>
          <p className="kicker">Sistema origen</p>
          <h2>Pedidos confirmados</h2>
        </div>
        <Button variant="quiet" onClick={onReset}>
          Reiniciar demo
        </Button>
      </div>
      <div className="order-grid">
        {orders.length ? (
          orders.map((order) => (
            <article className="order" key={order.order_id}>
              <h3>{order.order_id}</h3>
              <p>{order.producer_name}</p>
              <p>
                <strong>{order.current_volume_kg} kg</strong> · entrega{" "}
                {dateLocal(order.required_delivery_at)}
              </p>
              <span>{label(order.status)}</span>
            </article>
          ))
        ) : (
          <EmptyState
            title="Sin pedidos disponibles"
            description="Reinicia la demo para cargar pedidos en el ERP simulado."
            className="panel order-empty-state"
          />
        )}
      </div>
      <form className="panel form-panel" onSubmit={submit}>
        <div className="panel-title">
          Crear cambio desde ERP <small>Evento simulado</small>
        </div>
        <div className="form-grid">
          <label>
            Pedido
            <select
              value={selected || orders[0]?.order_id || ""}
              onChange={(event) => setSelected(event.target.value)}
            >
              {orders.map((order) => (
                <option value={order.order_id} key={order.order_id}>
                  {order.order_id} — {order.producer_name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Nuevo volumen (kg)
            <input
              type="number"
              min="1"
              value={volume}
              onChange={(event) => setVolume(event.target.value)}
              required
            />
          </label>
          <label>
            Fecha requerida
            <input
              type="datetime-local"
              value={delivery}
              onChange={(event) => setDelivery(event.target.value)}
              required
            />
          </label>
          <label className="wide">
            Motivo
            <input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              required
            />
          </label>
        </div>
        <Button>Registrar cambio y notificar</Button>
      </form>
    </section>
  );
}

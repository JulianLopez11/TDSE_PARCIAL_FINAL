import { useCallback, useEffect, useState } from "react";
import { Dashboard, Order, origenApi } from "../services/api";

const emptyDashboard: Dashboard = {
  total_changes: 0,
  pending_producer_response: 0,
  pending_human_review: 0,
  confirmed: 0,
  urgent: 0,
  escalated: 0,
  commitments: [],
  changes: [],
};

export function useOrigenData() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [dashboard, setDashboard] = useState<Dashboard>(emptyDashboard);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    try {
      const [erp, data] = await Promise.all([
        origenApi.orders(),
        origenApi.dashboard(),
      ]);
      setOrders(erp.orders);
      setDashboard(data);
      setError("");
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "No se pudo conectar con el backend.",
      );
    }
  }, []);
  useEffect(() => {
    void refresh();
    const refreshId = window.setInterval(() => void refresh(), 15_000);
    return () => window.clearInterval(refreshId);
  }, [refresh]);
  return { orders, dashboard, error, setError, refresh };
}

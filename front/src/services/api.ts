export type Order = {
  order_id: string;
  producer_id: string;
  producer_name: string;
  current_volume_kg: number;
  required_delivery_at: string;
  status: string;
};
export type Change = {
  change_id: string;
  request_id: string;
  source_event_id: string;
  commitment_id: string;
  version: number;
  status: string;
  order: {
    order_id: string;
    producer_id: string;
    previous_volume_kg: number;
    new_volume_kg: number;
    required_delivery_at: string;
    reason: string;
  };
  risk: {
    level: string;
    lead_hours: number;
    volume_variation_pct: number;
    urgent: boolean;
    high_impact: boolean;
    requires_human_approval: boolean;
  };
  rag_explanation: {
    text: string;
    sources: { id: string; title: string }[];
    limitation: string;
  };
  notification: {
    recipient: string;
    subject: string;
    delivery_status: string;
    error?: string;
  };
  producer_response?: { action: string; comment: string };
  escalation?: { reason: string; owner: string; at: string; status: string };
  audit_log: { event: string; at: string; actor: string }[];
};
export type Dashboard = {
  total_changes: number;
  pending_producer_response: number;
  pending_human_review: number;
  confirmed: number;
  urgent: number;
  escalated: number;
  commitments: {
    commitment_id: string;
    order_id: string;
    status: string;
    version_count: number;
  }[];
  changes: Change[];
};
export type NewChange = {
  order_id: string;
  producer_id: string;
  source_event_id?: string;
  previous_volume_kg: number;
  new_volume_kg: number;
  required_delivery_at: string;
  changed_at: string;
  reason: string;
};

export type AgentTask = {
  task_id: string;
  instruction: string;
  status: string;
  created_at: string;
  message?: string;
  draft_id?: string;
  change_id?: string;
  steps: string[];
};

export type EmailDraft = {
  draft_id: string;
  change_id: string;
  recipient: string;
  subject: string;
  body: string;
  status: string;
  created_at: string;
  send_attempts: number;
  error?: string;
};

export type AgentSummary = {
  open_commitments: number;
  awaiting_producer: number;
  escalated: number;
};

export type InboxSyncResult = {
  status: string;
  processed: string[];
  ignored: string[];
};

const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const data: unknown = await response.json();
  if (!response.ok)
    throw new Error(
      (data as { detail?: string }).detail ?? "No fue posible completar la operación.",
    );
  return data as T;
}

export const origenApi = {
  orders: () => request<{ orders: Order[] }>("/erp/pedidos"),
  dashboard: () => request<Dashboard>("/dashboard"),
  createChange: (body: NewChange) =>
    request<Change>("/cambios-pedido", { method: "POST", body: JSON.stringify(body) }),
  respond: (id: string, action: string) =>
    request(`/cambios-pedido/${id}/respuesta`, {
      method: "POST",
      body: JSON.stringify({
        action,
        comment: "Respuesta registrada desde el portal simulado.",
      }),
    }),
  approve: (id: string, approved: boolean) =>
    request(`/cambios-pedido/${id}/aprobacion`, {
      method: "POST",
      body: JSON.stringify({
        approved,
        approver: "Responsable de compras",
        comment: "Decisión tomada en el prototipo.",
      }),
    }),
  reset: () => request("/demo/reiniciar", { method: "POST" }),
  activities: () =>
    request<{
      activities: AgentTask[];
      drafts: EmailDraft[];
      summary: AgentSummary;
    }>("/agent/activities"),
  updateDraft: (
    draftId: string,
    draft: Pick<EmailDraft, "recipient" | "subject" | "body">,
  ) =>
    request<EmailDraft>(`/agent/emails/${draftId}`, {
      method: "PATCH",
      body: JSON.stringify(draft),
    }),
  sendDraft: (draftId: string) =>
    request<EmailDraft>(`/agent/emails/${draftId}/send`, {
      method: "POST",
      body: JSON.stringify({
        confirmed: true,
        idempotency_key: crypto.randomUUID(),
      }),
    }),
  syncInbox: () => request<InboxSyncResult>("/agent/inbox/sync", { method: "POST" }),
};

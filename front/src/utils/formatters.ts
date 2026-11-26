const labels: Record<string, string> = {
  AWAITING_CONFIRMATION: "Pendiente de confirmación",
  AWAITING_HUMAN_APPROVAL: "Pendiente de aprobación humana",
  CANCELLED: "Cancelado",
  COMPLETED: "Completado",
  EMAIL_FAILED: "Error de correo",
  ESCALATED: "Escalado",
  FAILED: "Error",
  NOTIFIED: "Notificado",
  PENDING_HUMAN_APPROVAL: "Pendiente de aprobación humana",
  PENDING_HUMAN_REVIEW: "Pendiente de revisión humana",
  PENDING_PRODUCER_RESPONSE: "Esperando productor",
  SENT: "Enviado",
  SUPERSEDED: "Reemplazado por una versión posterior",
};

export const label = (value: string) => labels[value] ?? value.replaceAll("_", " ");
export const dateLocal = (value: string) => new Date(value).toLocaleString("es-CO");

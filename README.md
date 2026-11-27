# Origen Acuerdos - Prototipo funcional

Prototipo académico para el caso Café Origen. Incluye un frontend en **React +
TypeScript** y un backend en **FastAPI**. Simula un ERP, registra cambios de
pedido, calcula el riesgo, registra la respuesta del productor y expone un
agente operativo automático con borradores de correo y trazabilidad de sus
tareas.

Cada pedido se gestiona como un compromiso trazable. Los eventos del ERP crean
versiones consecutivas, evitan duplicados mediante `source_event_id` y conservan
la evidencia de notificación, respuesta, escalamiento y decisión humana.

## Estructura

```text
back/   API FastAPI, reglas, RAG acotado y ERP simulado
front/  React + TypeScript (Vite), interfaz del prototipo
```

El frontend se organiza para que cada archivo tenga una responsabilidad clara:

```text
front/src/
├── assets/       # Estilos globales y futuros recursos visuales
├── components/   # Button, Navigation y CaseCard reutilizables
├── hooks/        # useOrigenData: estado y carga compartida
├── pages/        # ERP, productor, control y trazabilidad
├── services/     # Cliente HTTP tipado hacia FastAPI
├── utils/        # Formateo de fechas y estados
├── App.tsx       # Layout y composición de pantallas
└── main.tsx      # Punto de entrada React
```

El ERP permanece simulado. El envío de correo usa SMTP real cuando se configuran
las credenciales solo en el backend. Sin credenciales, el agente indica que SMTP
no está disponible y nunca presenta un envío como exitoso.

## Requisitos

- Python 3.11 o superior

## Instalación y ejecución

En una terminal, inicia el backend:

```powershell
cd back
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

En otra terminal, inicia el frontend:

```powershell
cd front
npm install
npm run dev
```

La documentación interactiva estará disponible en:

```text
http://127.0.0.1:8000/docs
```

La demostración visual estará disponible en:

```text
http://127.0.0.1:5173/
```

## Recorrido de demostración

1. En **ERP simulado**, selecciona el pedido y registra un cambio.
2. En **Portal del productor**, acepta, rechaza o pide revisión.
3. En **Control de compras**, aprueba o rechaza los cambios urgentes o de alto impacto.
4. En **Trazabilidad**, revisa el identificador de solicitud, política consultada,
   notificación simulada y eventos registrados.
5. Al registrar el cambio, **Agente** detecta el evento, valida los datos, consulta
   las políticas aprobadas, crea un caso y prepara el correo. En **Agente** revisa,
   edita y confirma el envío SMTP cuando la automatización no ha sido autorizada.

## Endpoints

| Método | Endpoint | Propósito |
|---|---|---|
| `GET` | `/health` | Verifica que la API está activa. |
| `GET` | `/fuentes` | Expone las políticas ficticias permitidas para el RAG. |
| `POST` | `/cambios-pedido` | Simula un evento proveniente del ERP. |
| `POST` | `/cambios-pedido/{change_id}/respuesta` | Registra aceptar, rechazar o pedir revisión. |
| `POST` | `/cambios-pedido/{change_id}/aprobacion` | Simula decisión humana para casos sensibles. |
| `GET` | `/cambios-pedido/{change_id}` | Consulta trazabilidad completa. |
| `GET` | `/agent/activities` | Devuelve historial y borradores del agente. |
| `POST` | `/agent/monitor` | Ejecuta la revisión de casos sin respuesta; la API también la programa cada 15 segundos por defecto. |
| `PATCH` | `/agent/emails/{draft_id}` | Edita un borrador pendiente. |
| `POST` | `/agent/emails/{draft_id}/send` | Confirma y envía correo por SMTP real. |
| `GET` | `/agent/inbox/status` | Consulta si la recepción IMAP está configurada. |
| `POST` | `/agent/inbox/sync` | Lee respuestas nuevas de correo y actualiza el caso trazable. |

## Configurar correo SMTP real

1. Copia `back/.env.example` como `back/.env`.
2. Completa los valores de tu proveedor SMTP. No subas ese archivo al repositorio.
3. Para Gmail usa `SMTP_PORT=465`, `SMTP_USE_SSL=true` y `SMTP_USE_TLS=false`.
   Otros proveedores pueden requerir `587` con `SMTP_USE_TLS=true`.
4. Carga las variables en la sesión donde ejecutas Uvicorn. En PowerShell, por
   ejemplo: `$env:SMTP_HOST = "smtp.tu-proveedor.com"`.
5. Define `AGENT_AUTO_SEND_EMAIL=false` para que el agente cree un borrador que
   una persona pueda revisar y confirmar desde la aplicación. Define el valor en
   `true` solo si existe una autorización explícita de envío automático.
6. El servidor usa `smtplib`, espera la respuesta SMTP y solo después marca el
   correo como `SENT`.

## Recibir respuestas por correo

SMTP solo envía correos. Para que una respuesta como `ACEPTO`, `RECHAZO` o
`SOLICITO REVISIÓN` actualice el caso, activa el receptor en `back/.env`:

```env
AGENT_INBOUND_EMAIL_ENABLED=true
```

Con Gmail, el receptor reutiliza automáticamente `SMTP_HOST`, `SMTP_USERNAME`
y `SMTP_PASSWORD`; no debes duplicar credenciales. Para otro proveedor, define
`IMAP_HOST`, `IMAP_PORT`, `IMAP_USERNAME` e `IMAP_PASSWORD`. El agente revisa
cada 15 segundos por defecto los mensajes recientes de productores registrados e identifica el
caso con `CAM-` en el asunto o en el cuerpo; también se puede forzar una
lectura con `POST /agent/inbox/sync` o desde `POST /agent/monitor`. Solo acepta
respuestas que provengan del correo registrado para el productor y que incluyan
el identificador `CAM-...` en el asunto del hilo. Los mensajes ajenos no se
marcan como leídos.

El prototipo no incluye credenciales ni cuenta de correo. Por eso, hasta que las
configures, cualquier intento de envío queda registrado como error y nunca se
presenta como exitoso. Esta es una restricción real, no una simulación de éxito.

## Persistencia local del prototipo

Los casos, versiones, borradores, actividades y respuestas se guardan en
`back/data/state.json`. De esta manera, un reinicio del backend no rompe la
trazabilidad ni la asociación entre una respuesta por correo y su caso. El
endpoint `POST /demo/reiniciar` limpia este estado solo cuando se desea empezar
una demostración desde cero.

## Caso válido reproducible

```powershell
$now = (Get-Date).ToUniversalTime()
$delivery = $now.AddHours(36)
$body = @{
  order_id = "OC-DEMO-001"
  producer_id = "PROD-001"
  previous_volume_kg = 500
  new_volume_kg = 650
  required_delivery_at = $delivery.ToString("o")
  changed_at = $now.ToString("o")
  reason = "Ajuste de demanda del canal de cafeterias."
} | ConvertTo-Json
$endpoint = "http://127.0.0.1:8000/cambios-pedido"
Invoke-RestMethod -Method Post -Uri $endpoint -ContentType "application/json" -Body $body
```

El resultado debe indicar `PENDING_PRODUCER_RESPONSE`, `urgent: true`, un
`request_id`, un borrador pendiente y las fuentes de política usadas para explicar
el cambio.

## Casos de error y conflicto

1. **Entrada inválida:** envía un valor negativo en `new_volume_kg`. La API
   devuelve `422` y explica el campo inválido.
2. **Conflicto:** registra una respuesta de productor y repite el mismo endpoint.
   La API devuelve `409` para evitar respuestas duplicadas.

## Pruebas

```powershell
cd back
python -m pytest -q -p no:cacheprovider test_app.py
```

Las pruebas desactivan `AGENT_AUTO_SEND_EMAIL` de forma temporal para no enviar
correos reales. Cubren un caso válido (`201`), una entrada inválida (`422`), un
conflicto por respuesta duplicada (`409`), idempotencia de eventos, versiones y
escalamiento por rechazo o falta de respuesta.

## Calidad de código

El proyecto deja la configuración de formato y verificación en el repositorio.
Antes de entregar se pueden ejecutar estos comandos:

```powershell
# Backend (instala herramientas de desarrollo una vez)
cd back
python -m pip install -r requirements-dev.txt
python -m ruff format --check .
python -m ruff check .

# Frontend
cd ../front
npm run format:check
npm run build
```

## Límites de autonomía y gobierno

- El RAG solo consulta `POL-PROD-01` y `POL-PROD-02`, fuentes ficticias y
  aprobadas para el piloto.
- El agente se activa ante cada evento de cambio del ERP: valida datos, consulta el
  RAG acotado, crea un caso, prepara o envía la notificación según autorización y
  registra cada paso. No modifica pedidos definitivos, pagos ni contratos.
- El monitor automático escala a compras los cambios sin respuesta al vencer el
  plazo definido. Los rechazos y solicitudes de revisión también se escalan.
- El RAG del piloto recupera únicamente políticas aprobadas mediante coincidencia
  de términos; explica la decisión, pero no autoriza cambios finales.
- Un cambio urgente o de alto impacto no se confirma sin aprobación humana.
- El envío SMTP requiere confirmación explícita. Las credenciales viven solo en
  variables de entorno del backend; no se devuelven al frontend.

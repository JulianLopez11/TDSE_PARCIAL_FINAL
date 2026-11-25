# Origen Acuerdos - Prototipo funcional

Prototipo académico para el caso Café Origen. Incluye un frontend en **React +
TypeScript** y un backend en **FastAPI**. Simula un ERP, registra cambios de
pedido, calcula el riesgo, registra la respuesta del productor y expone un
agente operativo con borradores de correo y trazabilidad de sus tareas.

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
   edita y confirma el envío SMTP; también puedes consultar el estado de un caso.

## Endpoints

| Método | Endpoint | Propósito |
|---|---|---|
| `GET` | `/health` | Verifica que la API está activa. |
| `GET` | `/fuentes` | Expone las políticas ficticias permitidas para el RAG. |
| `POST` | `/cambios-pedido` | Simula un evento proveniente del ERP. |
| `POST` | `/cambios-pedido/{change_id}/respuesta` | Registra aceptar, rechazar o pedir revisión. |
| `POST` | `/cambios-pedido/{change_id}/aprobacion` | Simula decisión humana para casos sensibles. |
| `GET` | `/cambios-pedido/{change_id}` | Consulta trazabilidad completa. |
| `POST` | `/agent/tasks` | Ejecuta una instrucción permitida del agente. |
| `GET` | `/agent/activities` | Devuelve historial y borradores del agente. |
| `GET` | `/agent/integrations` | Expone disponibilidad de herramientas sin secretos. |
| `PATCH` | `/agent/emails/{draft_id}` | Edita un borrador pendiente. |
| `POST` | `/agent/emails/{draft_id}/send` | Confirma y envía correo por SMTP real. |

## Configurar correo SMTP real

1. Copia `back/.env.example` como `back/.env`.
2. Completa los valores de tu proveedor SMTP. No subas ese archivo al repositorio.
3. Para Gmail usa `SMTP_PORT=465`, `SMTP_USE_SSL=true` y `SMTP_USE_TLS=false`.
   Otros proveedores pueden requerir `587` con `SMTP_USE_TLS=true`.
4. Carga las variables en la sesión donde ejecutas Uvicorn. En PowerShell, por
   ejemplo: `$env:SMTP_HOST = "smtp.tu-proveedor.com"`.
5. Abre **Agente**, crea un borrador y confirma el envío. El servidor usa
   `smtplib`, espera la respuesta SMTP y solo después marca el correo como `SENT`.

El prototipo no incluye credenciales ni cuenta de correo. Por eso, hasta que las
configures, SMTP aparecerá como **No configurado**. Esta es una restricción real,
no una simulación de éxito.

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

El resultado debe indicar `PENDING_PRODUCER_RESPONSE`, `urgent: true`, una
notificación simulada y las fuentes de política usadas para explicar el cambio.

## Casos de error y conflicto

1. **Entrada inválida:** envía el mismo valor en `previous_volume_kg` y
   `new_volume_kg`. La API devuelve `409` porque no existe cambio que comunicar.
2. **Conflicto:** registra una respuesta de productor y repite el mismo endpoint.
   La API devuelve `409` para evitar respuestas duplicadas.

## Pruebas

```powershell
cd back
python -m pytest -q
```

Las tres pruebas cubren un caso válido, una entrada inválida y un conflicto por
respuesta duplicada.

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
  RAG acotado, crea un caso, prepara la notificación y registra cada paso. No modifica
  pedidos definitivos, pagos ni contratos.
- Un cambio urgente o de alto impacto no se confirma sin aprobación humana.
- El envío SMTP requiere confirmación explícita. Las credenciales viven solo en
  variables de entorno del backend; no se devuelven al frontend.

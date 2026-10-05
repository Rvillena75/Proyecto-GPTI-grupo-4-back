# StockSmart — backend de inventario P1–P5

Este repositorio contiene **solo el backend**. Al 28 de septiembre de 2026, el Goal 1 implementa el núcleo de inventario **P1–P5 y D1–D7**: insumos y stock, movimientos y conteos, consumo, proyección, alertas y cantidad base a reponer. No incluye frontend. Las reglas funcionales están en [`PLAN_BACKEND_STOCKSMART.md`](PLAN_BACKEND_STOCKSMART.md) y las precisiones D1–D7 en [`docs/decisions.md`](docs/decisions.md).

## Estado de implementación

**Implementado en este repositorio:**

- API REST JSON con OpenAPI para el núcleo P1–P5.
- Stock inicial, entradas, salidas, conteos y saldo en PostgreSQL.
- Historial inmutable; reversas y correcciones trazables; validación de saldo y conciliaciones históricas D1–D7.
- Cálculo P3 de faltantes y tasa por período. La función matemática de reducción frente a una tasa base está implementada y probada.
- Cálculo P4 de consumo y proyección, y reglas P5 de stock seguro automático/manual, alertas y cantidad base a reponer.

**Pendiente de implementar:**

- **P6:** validar las fuentes y sus condiciones de uso; después, implementar adaptadores de proveedores. No hay consultas de precios reales.
- **P7:** formatos comerciales, comparación de ofertas, recomendación de proveedor y ahorro potencial por orden.
- **P8:** autenticación, roles y permisos, decisión humana y registro de órdenes internas.
- **P9:** carga/gestión de línea base y métricas comparativas completas del piloto. La reducción matemática implementada no incluye la gestión de esa línea base.
- **P10:** respaldos automáticos, retención y prueba de restauración.
- **P11:** documentar e implementar las rutas para precios, sugerencias, decisiones, órdenes y métricas; acordar con el equipo de frontend las decisiones que dependan de su integración.
- **P12:** despliegue y servicios operacionales. El núcleo actual utiliza Python, FastAPI, PostgreSQL, SQLAlchemy y Alembic; no incluye autenticación ni despliegue configurado.

El alcance y el estado detallado por requisito están en [`PLAN_BACKEND_STOCKSMART.md`](PLAN_BACKEND_STOCKSMART.md). Las metas de reducción de quiebres, merma y ahorro siguen siendo métricas pendientes de evaluación con datos comparables, no resultados del software.

## Requisitos y base de datos

- Python 3.11 o posterior y PostgreSQL 17 o posterior.
- Una base de aplicación y otra base **exclusiva para tests**, cuyo nombre termine en `_test`.
- Credenciales locales propias. No uses datos reales del negocio para pruebas.

Inicia tu servicio PostgreSQL. En Windows puedes localizarlo con `Get-Service *postgres*` y arrancarlo con `Start-Service <nombre-del-servicio>` si está detenido; en Linux normalmente se usa `sudo systemctl start postgresql`. Crea el usuario y las bases con una cuenta administradora de PostgreSQL (reemplaza la contraseña de ejemplo):

```sql
CREATE USER stocksmart WITH PASSWORD 'elige-una-clave-local';
CREATE DATABASE stocksmart OWNER stocksmart;
CREATE DATABASE stocksmart_test OWNER stocksmart;
```

Puedes ejecutar cada sentencia con `psql -U postgres -c "..."`; `CREATE DATABASE` debe ejecutarse fuera de una transacción. En este proyecto la URL usa el controlador `psycopg`:

```text
postgresql+psycopg://stocksmart:<clave>@localhost:5432/stocksmart
```

## Instalar y configurar

Desde `backend/`, en PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
$env:STOCKSMART_DATABASE_URL = "postgresql+psycopg://stocksmart:<clave>@localhost:5432/stocksmart"
$env:STOCKSMART_TEST_DATABASE_URL = "postgresql+psycopg://stocksmart:<clave>@localhost:5432/stocksmart_test"
```

En bash, activa `.venv/bin/activate`, usa `python -m pip install -e '.[test]'` y exporta las dos variables. [`.env.example`](backend/.env.example) solo muestra los nombres de configuración: la aplicación lee **variables de entorno**, no carga un archivo `.env` automáticamente. Los archivos `.env` y datos de bases están excluidos de Git.

`STOCKSMART_CORS_ORIGINS` lista, separados por comas, los orígenes del frontend que pueden llamar a la API desde el navegador (por ejemplo `http://localhost:5173,https://stocksmart.vercel.app`). Sin esa variable la API no acepta llamadas de otros orígenes.

Aplica migraciones desde una base vacía y ejecuta la API:

```powershell
.\.venv\Scripts\alembic.exe -c alembic.ini upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

La API queda en `http://127.0.0.1:8000`; `/health` comprueba PostgreSQL, `/openapi.json` entrega el contrato JSON y `/docs` muestra la documentación interactiva generada por FastAPI. El usuario final del backend no necesita una interfaz creada en este repositorio.

## Tests y calidad

Desde `backend/`, con `STOCKSMART_TEST_DATABASE_URL` configurada:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check app tests migrations
.\.venv\Scripts\ruff.exe format --check app tests migrations
```

Los tests de integración usan PostgreSQL real. **Al iniciar la suite se elimina y recrea el esquema `public` de la base `_test` indicada** y se aplican las migraciones desde cero. Nunca apuntes esa variable a una base con datos que deban conservarse. La suite incluye concurrencia, rollback e inmutabilidad mediante triggers PostgreSQL. La migración `0002_balance_guard` también se aplica sobre una base existente en `0001_inventory`: verifica primero que los saldos ya coincidan y luego instala un trigger diferido que exige `items.current_stock = suma de efectos de movimientos` al confirmar cada transacción. Un `UPDATE` SQL aislado del saldo se rechaza. Las consultas de estado bloquean la fila del insumo mientras leen saldo e historial para entregar una visión coherente.

## Contrato REST JSON

Los campos decimales se devuelven como cadenas JSON para conservar precisión. Las fechas incluyen zona horaria y pueden mostrarse con otro desplazamiento UTC equivalente entre respuestas. Un error de negocio tiene forma `{"error":{"code":"NEGATIVE_STOCK","message":"..."}}`; los errores de validación de estructura usan el mismo sobre con `details`. OpenAPI documenta los cuerpos de error 422, 404 y 409 utilizados por cada ruta. Las cantidades persistidas usan `NUMERIC(24,6)` y se rechazan si exceden 18 dígitos enteros o seis decimales en la unidad base.

| Método y ruta | Operación |
|---|---|
| `GET /health` | Estado de la API y PostgreSQL. |
| `POST /items` | Crear insumo y stock inicial en una transacción. No acepta fecha inicial retroactiva. |
| `GET /items/{id}` | Saldo, observación, consumo, proyección, alerta y necesidad base. |
| `POST /items/{id}/initial-stock` | Nuevo stock inicial solo tras reversar válidamente el anterior. |
| `PUT /items/{id}/safe-stock` | Fijar umbral manual; `null` restaura el automático. |
| `POST /movements` | Entrada o salida, con fecha efectiva opcional; se validan historia y conteos. |
| `POST /movements/{id}/reverse` | Reversa exacta trazable si no rompe saldo ni conciliación. |
| `POST /movements/{id}/corrections` | Corregir en el presente un error seguido por conteo: `stock_effect=0` si ya fue absorbido, o un efecto firmado en unidad base si aún debe ajustarse el saldo actual. |
| `POST /counts` | Conteo y ajuste atómicos; `corrects_movement_id` vincula una corrección con efecto de stock. |
| `GET /items/{id}/movements` y `GET /items/{id}/counts` | Historial inmutable de solo lectura. |
| `GET /items/{id}/consumption` | Estado completo, incluido consumo y proyección. |
| `GET /items/{id}/alert` | Estado de alerta del insumo, incluso historia insuficiente. |
| `GET /alerts` | Alertas `LOW_STOCK` activas. |
| `GET /items/{id}/shortage-rate?start=...&end=...` | Tasa P3 para `[start,end)`; puede ser `NOT_CALCULABLE`. |

Ejemplo de creación:

```json
{"name":"Azúcar","base_unit":"kg","initial_quantity":"25","initial_unit":"kg","timezone":"America/Santiago"}
```

Ejemplo de salida: `POST /movements` con `{"item_id":1,"kind":"EXIT","quantity":"3","unit":"kg"}`. Se aceptan `kg`/`g`, `L`/`mL` y `unidad` en su familia respectiva; cada movimiento guarda la cantidad recibida y su efecto convertido a unidad base. Para registrar un conteo: `POST /counts` con `{"item_id":1,"physical_quantity":"22","unit":"kg","cause":"otra"}`.

Ejemplo de respuesta de `GET /items/1` inmediatamente después de crear el insumo (fechas ilustrativas):

```json
{"id":1,"name":"Azúcar","base_unit":"kg","timezone":"America/Santiago","current_stock":"25.000000","manual_safe_stock":null,"observation_started_at":"2026-09-28T12:00:00Z","created_at":"2026-09-28T12:00:00Z","updated_at":"2026-09-28T12:00:00Z","consumption":{"status":"INSUFFICIENT_HISTORY","complete_days":0,"observed_quantity":"0","weekly_average":null,"window_start":null,"window_end":null},"assessment":{"safe_stock":null,"alert_source":null,"alert_status":"INSUFFICIENT_HISTORY","is_low_stock":null,"target_stock":null,"suggested_quantity":null,"daily_average":null,"remaining_days":null,"depletion_at":null,"depletion_status":"INSUFFICIENT_HISTORY"}}
```

Una corrección descubierta después de un conteo conserva intacto ese conteo. Envía `{"note":"Error absorbido por conteo","stock_effect":"0"}` para constancia sin cambio de saldo. Si aún se necesita un ajuste presente de `+2 kg`, envía `{"note":"Ajuste actual documentado","stock_effect":"2"}`; un valor negativo reduce el saldo y se rechaza si lo dejaría bajo cero. `stock_effect` se expresa en la unidad base del insumo, se aplica al registrarse y **no** representa una reversa retroactiva ni una entrada/salida operativa. El movimiento de respuesta tiene `kind="CORRECTION"`, `delta` igual al efecto y `invalidates_movement_id` igual al movimiento señalado. También puede registrarse un **nuevo conteo físico** y enlazarlo con `corrects_movement_id`; su diferencia y faltante siguen P3. Las reversas exactas se rechazan cuando invalidarían un conteo posterior. Un movimiento retroactivo individual con efecto no nulo antes del primer conteo posterior cambia su saldo previo y se rechaza por D6. Si coincide exactamente en `occurred_at` con un conteo ya registrado, queda después de este por el orden `(occurred_at, created_at, id)` y sigue sujeto a los conteos posteriores. Si una corrección hace imposible reconstruir con confianza el denominador de P3, la tasa devuelve `NOT_CALCULABLE`.

`INSUFFICIENT_HISTORY` significa que todavía no hay un día calendario completo observado; no equivale a consumo cero. Con umbral manual puede evaluarse la alerta aun cuando consumo y cantidad sugerida sean no calculables. La ventana P4 usa días completos en la zona horaria configurada del insumo, excluye el día actual y nunca empieza antes de `observation_started_at`. `assessment.depletion_status` distingue `INSUFFICIENT_HISTORY`, `ZERO_CONSUMPTION`, `PROJECTED` y `OUT_OF_RANGE`. En este último caso el consumo y `remaining_days` siguen calculados, mientras `depletion_at` es `null`: no se presenta una fecha máxima artificial.

La fórmula matemática P3 de reducción de tasa frente a una línea base está implementada y probada. La carga/gestión de la línea base y la evaluación de métricas comparativas del piloto corresponden a P9; todavía no existen en esta API.

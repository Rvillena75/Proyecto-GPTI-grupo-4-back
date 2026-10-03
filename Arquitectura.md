# Documento de Arquitectura — StockSmart · Grupo 4
IIC3113-1 · Entrega 2 · Pedro Irarrázaval (líder de desarrollo + backend)
Versión 0.2 · 2026-10-03 — ajustada al repo `Proyecto-GPTI-grupo-4-back` (commit `efc0bd1`)

> **Qué saca cada uno:**
> - **Nati** → §6 y §7 van al DRS; §8 lista los REQ que hay que ajustar.
> - **Rodrigo** → §4.2 y §5.3 (precios y adaptadores), §8 y §9 (cambios al repo).
> - **Sofi** → §5 completo: el contrato real de la API, con lo que ya existe y lo que viene.
> - **Rai** → §10: paquetes de trabajo para la EDT y la estimación de HH (lun 05-10).
> - **Fran** → §2 para el diagrama del PPT; §11 para el Registro de Riesgos.

---

## 1. Decisiones de arquitectura

| # | Decisión | Estado |
|---|---|---|
| D1 | App web: **frontend (React + TypeScript) y backend separados**, comunicados por API REST JSON | Acordado (plan P12) |
| D2 | Backend en **Python 3.11+ · FastAPI · SQLAlchemy 2 · Alembic · psycopg 3** | **Implementado** |
| D3 | **PostgreSQL** como única base; el saldo se protege con *triggers* en la propia base | **Implementado** |
| D4 | Historial **inmutable**: los errores se corrigen con reversas/correcciones, nunca editando | **Implementado** |
| D5 | Reglas de negocio en `app/domain` (Python puro, testeado), separadas de FastAPI (`app/api`) y de la base (`app/persistence`) | **Implementado** |
| D6 | Obtención de precios en un **proceso aparte**, con **un adaptador por proveedor**; si un sitio falla, el resto sigue funcionando | Diseñado, sin código |
| D7 | **La compra nunca es automática**: el sistema sugiere y una persona aprueba | Diseñado, sin código |
| D8 | Despliegue: frontend en **Vercel**, API + base + tarea programada en **Render** | Propuesto (falta configurar) |

## 2. Vista general

```
   Navegador (PC / celular)
            │
   ┌────────▼─────────┐
   │ Frontend         │  React + TS · Vercel                     ← Sofi
   └────────┬─────────┘
            │ HTTPS · JSON · token (Bearer)
   ┌────────▼──────────────────────────────────────────┐
   │ API REST (FastAPI)                       Render   │
   │  app/api        rutas y esquemas (contrato)       │
   │  app/services   casos de uso transaccionales      │
   │  app/domain     reglas: consumo, alertas, merma,  │
   │                 comparación, ahorro               │
   │  app/persistence modelos SQLAlchemy               │
   └────────┬──────────────────────────────────────────┘
            │ SQL
   ┌────────▼─────────┐        ┌─────────────────────────────┐
   │ PostgreSQL       │◄───────┤ Worker de precios (cron)    │
   │ + migraciones    │ guarda │  scraper/adaptadores/*.py   │
   │   Alembic        │precios │  1 corrida diaria + "Actua- │
   └──────────────────┘        │  lizar precios" a pedido    │
                               └──────────────┬──────────────┘
                                              │ HTTPS (robots.txt, ≥2 s entre requests)
                                  Central Mayorista · Alvi (por validar)
```

**Stack:**

| Capa | Tecnología | Estado |
|---|---|---|
| Frontend | React + TypeScript | Mockup listo (Sofi); código por empezar |
| API | FastAPI ≥0.115, Pydantic 2, Uvicorn | Implementado |
| Persistencia | SQLAlchemy 2, Alembic, PostgreSQL (probado en 14; README pide 17) | Implementado |
| Scraping | `httpx` + BeautifulSoup; Playwright sólo si el sitio renderiza con JS | Pendiente (validación 06-10) |
| Autenticación | JWT emitido por la propia API, contraseñas con bcrypt | **Decisión pendiente** (§8, C8) |
| Calidad | pytest (unit, integración con PostgreSQL real, contrato API), ruff | Implementado |
| Despliegue | Vercel (front) · Render web service + PostgreSQL + cron job (back) | Pendiente |

## 3. Qué hay hoy en el repo

| Plan | Qué cubre | Estado |
|---|---|---|
| P1 | Insumo con stock inicial (MVP: un insumo crítico) | ✅ |
| P2 | Entradas, salidas, unidades base (`kg`/`g`, `L`/`mL`, `unidad`), reversas y correcciones | ✅ |
| P3 | Conteo físico, ajuste atómico, faltante («merma») y tasa por período | ✅ |
| P4 | Consumo semanal (últimos 28 días completos), proyección de agotamiento | ✅ |
| P5 | Stock seguro automático (1 semana) o manual, alerta, cantidad a reponer (objetivo 2 semanas) | ✅ |
| D1–D7 | Inmutabilidad, movimientos retroactivos, conteos como puntos de conciliación | ✅ |
| P6 | Fuentes de precios y adaptadores | ❌ |
| P7 | Formatos comerciales, comparación, proveedor recomendado, ahorro | ❌ |
| P8 | Autenticación, roles, decisión humana, órdenes | ❌ |
| P9 | Línea base y panel de métricas | ❌ (sólo la fórmula de P3) |
| P10 | Respaldos | ❌ |
| P12 | Despliegue | ❌ |

Verificado el 2026-10-03: `pytest` → **54 passed**; `ruff check` → sin observaciones.

## 4. Modelo de datos

### 4.1 Existente (migraciones `0001_inventory`, `0002_balance_guard`)

| Tabla | Campos clave | Notas |
|---|---|---|
| `items` | id, name, base_unit (`kg`\|`L`\|`unidad`), timezone_name, current_stock, manual_safe_stock, created_at, updated_at | `current_stock ≥ 0` por *constraint*; un *trigger* exige que sea igual a la suma de los movimientos |
| `observations` | id, item_id, started_at, is_active | Inicio de la historia confiable (un activo por insumo) |
| `movements` | id, item_id, observation_id, **kind** (`INITIAL`\|`ENTRY`\|`EXIT`\|`COUNT_ADJUSTMENT`\|`REVERSAL`\|`CORRECTION`), delta, input_quantity, input_unit, occurred_at, created_at, invalidates_movement_id, note | Inmutable (*trigger*). Fuente del stock, del consumo y de la merma |
| `counts` | id, item_id, observation_id, adjustment_movement_id, corrects_movement_id, theoretical_before, physical_stock, difference, shortage, cause, occurred_at, created_at | Cada conteo genera su `COUNT_ADJUSTMENT` |

### 4.2 Por agregar (migraciones `0003`+, mismo estilo: inglés, `NUMERIC(24,6)`, fechas con zona)

| Tabla | Campos clave | Para qué (REQ del DRS) |
|---|---|---|
| `users` | id, email, password_hash, name, **role** (`ADMIN`\|`WAREHOUSE`), is_active | REQ-14. Agregar `created_by` a `movements` y `counts` (el mockup muestra el usuario en el historial) |
| `suppliers` | id, name, price_source_url, **lead_time_days**, shipping_cost, contact, adapter, is_active | REQ-13, REQ-18 (tiempo de entrega) |
| `supplier_products` | id, supplier_id, item_id, name_on_site, url, **pack_quantity** (en unidad base), min_packs | P7: compara formatos completos (saco 25 kg → 25) |
| `price_quotes` | id, supplier_product_id, run_id (nullable), **source** (`SCRAPING`\|`MANUAL`), pack_price_clp, unit_price_clp, available, observed_at, created_by | REQ-07, REQ-15 (carga manual). Se guarda el histórico completo |
| `scrape_runs` | id, supplier_id, started_at, finished_at, status (`OK`\|`PARTIAL`\|`ERROR`), message | Registro de fallos que muestra el mockup en *Proveedores* |
| `suggestions` | id, item_id, status (`PENDING`\|`APPROVED`\|`REJECTED`), required_qty, supplier_product_id, packs, purchased_qty, total_clp, reference_total_clp, savings_clp, inputs_snapshot (JSON), created_at | P8: guarda los datos y precios usados, para explicar la sugerencia |
| `suggestion_revisions` | id, suggestion_id, changed_by, changed_at, before (JSON), after (JSON) | P8: el admin puede cambiar cantidad/proveedor antes de aprobar, con historial |
| `orders` | id, suggestion_id, code (`OC-0012`), status (`APPROVED`\|`RECEIVED`), decided_by, decided_at, received_at, receipt_movement_id | REQ-10, REQ-16. La recepción crea un `ENTRY` y lo enlaza (se mantiene el libro inmutable) |

### 4.3 Reglas de cálculo vigentes en el código (van al DRS como *Reglas de negocio*)

- **Consumo semanal** = salidas de los últimos 28 días completos / 4 (días locales, `America/Santiago`; excluye el día en curso). Con < 7 días: *estimación preliminar*; sin un día completo: no se calcula.
- **Stock seguro** = consumo semanal (automático) o el valor manual si existe.
- **Alerta** si `stock actual ≤ stock seguro`. ⚠ Hoy **no** considera el tiempo de entrega (ver C1).
- **Cantidad a reponer** = `max(0, 2 × consumo semanal − stock actual)`; después se redondea a formatos completos. ⚠ El mockup usa otra lógica (ver C5).
- **Merma** = `max(teórico − físico, 0)` por conteo; **tasa** = faltantes del período / (stock inicial del período + entradas + ajustes positivos).
- **Ahorro potencial** = (costo de referencia − costo sugerido) / costo de referencia. ⚠ Falta acordar la referencia (ver C6).

## 5. Contrato de la API

### 5.1 Convenciones (las del repo, ya implementadas)

- JSON; rutas y campos en **inglés** y `snake_case`; **sin prefijo de versión** (`/items`, no `/api/v1/items`).
- **Decimales como texto** (`"25.000000"`) para no perder precisión → el frontend debe convertirlos (`Number(x)` para mostrar).
- Fechas ISO 8601 con zona; pueden volver en UTC (`...Z`): el frontend las muestra en hora de Chile.
- Errores siempre con el mismo sobre: `{"error": {"code": "NEGATIVE_STOCK", "message": "...", "details": [...]}}`.
  `404` no existe · `409` regla de negocio · `422` validación. **Los `code` están en inglés y el `message` también: el frontend traduce por `code`.**
- Documentación viva en `/docs` (Swagger) y `/openapi.json`: **es el contrato oficial**.

### 5.2 Endpoints existentes

| Método y ruta | Qué hace | Pantalla del mockup |
|---|---|---|
| `GET /health` | Estado de API y base | — |
| `POST /items` | Crear insumo + stock inicial | Insumos → Nuevo insumo |
| `GET /items/{id}` | Stock, consumo, proyección, alerta, cantidad sugerida | Panel de stock |
| `PUT /items/{id}/safe-stock` | Stock seguro manual (`null` = automático) | Insumos → Editar |
| `POST /items/{id}/initial-stock` | Reemplazar stock inicial (sólo tras reversarlo) | — |
| `POST /movements` | Entrada o salida (`kind`: `ENTRY`\|`EXIT`) | Movimientos |
| `POST /movements/{id}/reverse` · `/corrections` | Anular o corregir un movimiento | — |
| `POST /counts` | Conteo físico + ajuste | Conteo y merma |
| `GET /items/{id}/movements` · `/counts` | Historiales | Movimientos · Conteo y merma |
| `GET /items/{id}/consumption` | Igual que `GET /items/{id}` | — |
| `GET /items/{id}/alert` · `GET /alerts` | Alerta de un insumo / alertas activas | Panel, badge del menú |
| `GET /items/{id}/shortage-rate?start&end` | Tasa de merma del período | Conteo y merma |

### 5.3 Endpoints por agregar

| Método y ruta | Qué hace | REQ | Brecha que cubre |
|---|---|---|---|
| `GET /items` | **Listar insumos** | REQ-01 | El mockup lista insumos; hoy sólo existe `GET /items/{id}` |
| `PATCH /items/{id}` | Editar nombre (y proveedores asociados) | REQ-01 | Hoy sólo se puede editar el stock seguro |
| `POST /auth/login` · `GET /auth/me` | Sesión y usuario actual | REQ-14 | — |
| `GET/POST /suppliers` · `PATCH /suppliers/{id}` | Proveedores con URL y días de entrega | REQ-13, REQ-18 | — |
| `GET /suppliers/{id}/scrape-runs` | Registro de fallos | REQ-07 | — |
| `GET /prices/{item_id}` | Ofertas vigentes/vencidas por proveedor, total para la cantidad requerida, mejor opción | REQ-08 | — |
| `POST /prices/{item_id}/refresh` | Botón «Actualizar precios» (corre los adaptadores) | REQ-07 | — |
| `POST /prices/{item_id}/manual` | Carga manual de precio | REQ-15 | Ver C2 |
| `POST /suggestions` · `GET /suggestions/{id}` · `PATCH /suggestions/{id}` | Generar, ver y ajustar la sugerencia | REQ-09 | — |
| `POST /suggestions/{id}/decision` | `{"decision": "APPROVE"\|"REJECT", "reason"}` → crea la orden; sólo `ADMIN` | REQ-10 | — |
| `GET /orders` · `POST /orders/{id}/receipt` | Historial; registrar recepción (crea `ENTRY`) | REQ-16 | Ver C3 |
| `GET /metrics?start&end` | Quiebres, merma, órdenes, ahorro, suficiencia de datos | REQ-12 | Ver C7 |

**Ajuste a una respuesta existente:** `MovementResponse` debería traer `balance_after` y `created_by`. La tabla de *Movimientos* del mockup muestra el **stock después de cada movimiento** y el **usuario**, y el frontend no puede calcular bien el saldo cuando hay reversas o movimientos retroactivos.

### 5.4 Contrato de los adaptadores (Rodrigo)

`scraper/adaptadores/<proveedor>.py` implementa `fetch(url) -> RawQuote(name_on_site, price_text, pack_text, available, url)`.
La normalización (`"$18.990"` → `18990`; `"Saco 25 Kg"` → `pack_quantity = 25`) va en un único `normalize.py`.
Un adaptador que falla lanza una excepción: la corrida queda `PARTIAL` y se registra en `scrape_runs`, sin botar a los demás.
Si R12 se confirma (catálogo o API pública), ese proveedor usa un adaptador de API con la misma interfaz.

## 6. Entorno operativo (para el DRS §6)

- **Cliente:** navegador moderno (Chrome, Safari, Edge, Firefox; últimas 2 versiones), en computador o celular, responsive desde 360 px. Sin instalación.
- **Servidor:** nube. Render (Linux) para la API (Python 3.11+, FastAPI/Uvicorn), la base PostgreSQL y la tarea programada de precios. Vercel para el frontend (Node 20 sólo para compilar).
- **Conectividad:** requiere internet; todo el tráfico va por HTTPS.
- **Coexiste con:** los sitios públicos de los proveedores (fuente de precios). Sin integración con ERP, POS ni contabilidad en el MVP.
- **Zona horaria:** configurable por insumo; el piloto usa `America/Santiago`.

## 7. Interfaces externas y requerimientos no funcionales (para el DRS §9–§10)

| Tipo | Contenido |
|---|---|
| Usuario | Web responsive en español: login, panel de stock, movimientos, conteo y merma, precios, órdenes, métricas, insumos, proveedores (el mockup de Sofi) |
| Hardware | Ninguna específica |
| Software | PostgreSQL; sitios de Central Mayorista y Alvi (por validar) |
| Comunicación | HTTPS/TLS; API REST JSON con token Bearer; contrato publicado en OpenAPI |

**No funcionales:** saldo nunca negativo, garantizado por la base (ya implementado); historial inmutable (ya implementado); respaldo cada 24 h, 7 copias y una restauración probada antes de E3 (P10); si un proveedor falla, la app sigue funcionando y marca el precio como vencido; sólo `ADMIN` aprueba órdenes; contraseñas con hash.

## 8. Decisiones del equipo: repo vs. DRS vs. mockup

El **plan del repo** (P1–P12), el **DRS** de Nati y el **mockup** de Sofi se
escribieron en paralelo y no coinciden en estos puntos. Es justo el riesgo
**R05** (*inconsistencia entre documentos y aplicación*), y hay que cerrarlo
**antes de la integración 1 (09-10)**.

| # | Tema | Repo (plan) | DRS / mockup | Mi recomendación |
|---|---|---|---|---|
| C1 | **Alerta y tiempo de entrega** | Alerta si stock ≤ stock seguro; no usa días de entrega | REQ-06/18 y el panel («pide a más tardar hoy») anticipan la alerta con el tiempo de entrega | **Adoptar el DRS.** Alertar si `stock − consumo diario × días de entrega ≤ stock seguro`. Es un cambio chico en `domain/forecast.py`, con tests |
| C2 | **Carga manual de precios** | P6: no hay carga manual; sin dos precios vigentes no se recomienda | REQ-15 y mockup: carga manual de respaldo, vigente 7 días | **Adoptar el DRS**, con `source = MANUAL` visible. De paso cierra el pendiente de P6: **vigencia de 7 días** para cualquier precio |
| C3 | **Recepción de la orden** | P8: aprobar no suma stock; no existe «recibida» | REQ-16 y mockup: estados sugerida → aprobada → recibida; la recepción suma stock | **Adoptar el DRS.** La recepción crea un `ENTRY` enlazado a la orden y respeta el libro inmutable |
| C4 | **Despacho** | P7: fuera del MVP salvo que sea comparable | El mockup suma despacho al total | Incluirlo como **costo fijo por proveedor, ingresado a mano**. El scraping no lo obtiene |
| C5 | **Cantidad sugerida** | `2 × consumo semanal − stock actual` (≈ 5 u en el ejemplo del mockup) | El mockup sugiere 20 u («≈3,5 semanas») | Acordar **una** fórmula. Propongo `objetivo 2 semanas − stock proyectado a la llegada`, redondeado a formatos. El mockup tiene que mostrar el número que da el backend |
| C6 | **Referencia del ahorro** | P7: contra el **proveedor habitual** del piloto | Mockup: contra la **siguiente mejor opción vigente** | La del mockup se puede calcular siempre; la del plan necesita definir el proveedor habitual. Elegir una y usarla en todo (DRS, panel, informe) |
| C7 | **«Quiebres evitados»** | P9: cuenta quiebres y los compara con una línea base | REQ-12 y mockup: «quiebres evitados» | No es medible tal cual. Usar **«alertas atendidas antes del quiebre»** (el mockup ya lo dice en el subtítulo) + quiebres del período |
| C8 | **Autenticación** | P12: «PostgreSQL + autenticación con Supabase» | REQ-14, sin detalle | **JWT en la propia API** (tabla `users`). Evita amarrar el frontend a Supabase y mantiene una sola base que respaldar (P10) |
| C9 | **Numeración de REQ** | — | La *Lista de funcionalidades* y el DRS numeran distinto: REQ-15 es «recepción» en una y «carga manual» en la otra; historial es 16 vs 17; tiempo de reposición 17 vs 18 | Congelar la del **DRS** (REQ-01…18) y usarla en el Plan de Pruebas y el repo |
| C10 | **Conteo mayor al teórico** | Ajuste positivo, sin faltante | REQ-11 y mockup: marcarlo «Revisar» | Compatible: agregar `needs_review = difference > 0` a la respuesta del conteo |

## 9. Lo que está bien y lo que está mal en el repo

**Bien:**
- Arquitectura limpia y en capas (`domain` / `services` / `api` / `persistence`); las reglas se prueban sin levantar la API.
- **54 tests pasan** (unitarios, integración con PostgreSQL real, contrato API) y `ruff` no marca nada. También corre en PostgreSQL 14, no sólo en 17.
- El saldo y el historial inmutable están protegidos **en la base** con *triggers*, no sólo en el código.
- Contrato OpenAPI y sobre de error uniforme: le sirve a Sofi como contrato vivo.
- Sin secretos en el repo; configuración por variables de entorno.
- Plan y decisiones (P1–P12, D1–D7) documentados con trazabilidad a la E1: sirven para el DRS y el Plan de Pruebas.

**Mal o por corregir (en orden de urgencia):**
1. **No tiene CORS.** Un frontend en Vercel (u otro origen) no va a poder llamar a la API desde el navegador. Hay que agregar `CORSMiddleware` con el origen del frontend **antes del 09-10**.
2. **Cubre ~40 % del DRS.** Faltan REQ-07, 08, 09, 10, 12, 13, 14, 15 y 16 (precios, órdenes, auth, métricas). La demo es el 17-10: hay que priorizar el **flujo vertical** (insumo → alerta → precios → sugerencia → aprobación) por sobre más casos borde.
3. **Diverge del DRS y del mockup** en C1–C7. Si no se decide ahora, Sofi construye pantallas que el backend no puede alimentar.
4. **Sobredimensionado para el MVP.** D5–D7 (movimientos retroactivos, conteos como puntos de conciliación, reinicio de observación) son correctos pero no los pide ningún REQ, y cada módulo nuevo (recepción de órdenes) tiene que respetarlos. **Congelar: no más reglas D** hasta terminar el flujo completo.
5. **Faltan dos rutas para el mockup:** `GET /items` (listar) y `PATCH /items/{id}` (editar). Además, el historial no trae `balance_after` ni el usuario (§5.3).
6. **No hay despliegue.** Falta `render.yaml`/Procfile, ejecutar las migraciones al arrancar y el origen CORS por variable. Sin esto no hay *link a la aplicación* para la rúbrica (*Evidencia de avance*, 2,5 pts).
7. **README orientado a Windows** (PowerShell). Agregar los comandos de macOS/Linux, porque yo trabajo en Mac. Además pide PostgreSQL 17 cuando funciona con 14: bajar el requisito o fijar 16, que es lo que ofrece Render.
8. **`GET /alerts` recalcula el historial completo de cada insumo.** Con un insumo da igual; si el piloto crece, conviene cachear el estado.
9. **Uso de IA.** `AGENTS.md` muestra que el repo se construyó con un agente. El curso exige **declararlo** (qué asistente, qué se pidió y cómo se validó) en los créditos de la E2.

### 9.1 La recomendación de arquitectura que ya está en el repo (P10–P12)

En GitHub sólo existe `main` (2 commits de Rodrigo, 28-09): no hay ramas,
PRs, issues ni repo de frontend. La recomendación de arquitectura vive en
`PLAN_BACKEND_STOCKSMART.md` (P10, P11, P12 y *inferencias de diseño*) y en
`AGENTS.md`. Contrastada con este documento:

| Recomendación del repo | ¿La sigo? | Comentario |
|---|---|---|
| Frontend React + TypeScript | ✅ | Igual |
| Backend Python + FastAPI | ✅ | Ya implementado |
| **PostgreSQL + autenticación mediante Supabase** | ⚠ Parcial | Cambio la parte de **auth** por JWT propio (C8). La base puede ser Supabase o Render indistintamente, porque el código sólo usa la URL `postgresql+psycopg://`. Ojo: el mismo plan (P10) advierte que **Supabase gratis no trae respaldos**, y además **pausa los proyectos inactivos**, lo que es un riesgo antes de la demo |
| Scraping `requests`/BeautifulSoup; Playwright sólo si hace falta | ✅ | Uso `httpx`, que ya es dependencia de tests; da lo mismo |
| Vercel (front) + Render/Railway (back) | ✅ | Elijo Render para tener API, base y *cron* en un solo lugar |
| API separada del scraping, con adaptadores por proveedor | ✅ | §2 y §5.4 |
| Sugerencia que guarda los datos y precios usados | ✅ | `suggestions.inputs_snapshot` (§4.2) |
| Contrato P11 (`/prices`, `/suggestions`, `/orders`, `/metrics`) | ✅ | Mantengo esos nombres y agrego lo que pide el DRS (§5.3) |
| Respaldo cada 24 h, 7 copias, restauración probada (P10) | ✅ | Paquete 8; propongo un *cron* de Render con `pg_dump` a almacenamiento privado |
| «Arquitectura simple y mantenible» (`AGENTS.md`) | ⚠ | El núcleo D5–D7 no es simple (punto 4 de §9) |

**Detalle menor del plan:** sus enlaces a las fuentes (`../Proyecto GPTI/Entrega 1/…`, `../../docs/decisions-and-questions.md`, `docs/course-guide.md`) apuntan a archivos que **no están en el repo** y salen rotos en GitHub.

## 10. Paquetes de trabajo técnicos (para la EDT de Rai)

1. **Base existente:** inventario P1–P5 ✅ (sólo mantener).
2. **Ajustes al núcleo:** CORS, `GET/PATCH /items`, `balance_after`/`created_by`, alerta con tiempo de entrega (C1), `needs_review` (C10).
3. **Autenticación y roles** (C8).
4. **Proveedores y precios:** tablas, carga manual, validación de fuentes (06-10), adaptador A, adaptador B, normalización, corrida programada, registro de fallos.
5. **Comparación y sugerencia** (P7, C4–C6).
6. **Decisión, órdenes y recepción** (P8, C3).
7. **Métricas** (P9, C7).
8. **Despliegue y respaldos** (P10, P12).
9. **Frontend** (Sofi): 9 pantallas del mockup + integración.
10. **Pruebas del Plan de Pruebas** (vinculadas a REQ-01…18) y **demo**.

## 11. Revisión de los planes técnicos del Registro de Riesgos v1 (Fran)

Riesgos a mi cargo, revisados contra el repo y el DRS:

| Riesgo | Observación | Propuesta |
|---|---|---|
| **R02** Bloqueo de sitios | El plan dice «si no hay precio actualizado, no usar ese proveedor». **Contradice REQ-15** (carga manual de respaldo) | «…se marca como vencido y se usa la **carga manual** (vigencia 7 días); si no hay dos precios vigentes, no se recomienda proveedor» |
| **R03** Integración Back–Front | **Está parcialmente activado:** el backend avanzó (28-09) sin acordar endpoints con el frontend, y ya hay brechas (§5.3, CORS) | Marcar «¿Activado? Sí · 2026-10-03» o, al menos, subir la probabilidad. Respuesta: este documento + OpenAPI como contrato + integración 1 el 09-10 |
| **R04** Retrabajo por diseño técnico | El plan dice «revisar la arquitectura **antes de comenzar** el desarrollo», pero el desarrollo ya empezó | «Congelar el modelo y el contrato el 05-10; los cambios posteriores pasan por las decisiones C1–C10 o por solicitud de cambio» |
| **R11** Condiciones de uso | Bien | Agregar evidencia: robots.txt y términos guardados en la carpeta del proyecto (06-10) |

Observaciones generales para Fran (no técnicas):
- **R05, R07 y R09** marcan sólo *Calidad* como objetivo afectado; la rúbrica pide **Alcance, Tiempo y/o Costo**. Marcar al menos uno en cada uno.
- **R10** tiene la fecha mal escrita (`21-09.2026`).
- **Riesgos que faltan:** (a) concentración del conocimiento del backend en una persona (el repo es complejo); (b) límites de los planes gratuitos de hosting (Render duerme el servicio; el día de la demo puede tardar ~1 min en despertar); (c) respaldos sin probar (P10).

# Documento de Arquitectura — StockSmart · Grupo 4
IIC3113-1 · Entrega 2 · Pedro Irarrázaval (líder de desarrollo + backend)
Versión 1.1 · 2026-10-05 — ajustada al repo `Proyecto-GPTI-grupo-4-back` (commit `efc0bd1`) y a las decisiones del equipo de la semana del 28-09

> **Qué saca cada uno:**
> - **Nati** → §4.3, §6 y §7 van al DRS (reglas de negocio, entorno, interfaces). La numeración oficial es la de 17 REQ (§8, C9).
> - **Rodrigo** → §4.2, §4.3 y §5.4 (precios, adaptadores y fórmulas); §12 (proveedores validados) y §13 (lo que te toca declarar).
> - **Sofi** → §5 completo: el contrato de la API. En §4.3 están los números que tienen que mostrar las pantallas.
> - **Rai** → §10 y `E2 Planificacion/Estimacion HH (Pedro).md`, para la EDT y el cronograma.
> - **Fran** → §2 para el diagrama del PPT; §11 y §12 para el Registro de Riesgos.

**Cambios respecto de la v1.0 (03-10):**
- Se elimina la carga manual de precios.
- La numeración pasa a 17 REQ.
- El ahorro se mide frente a la siguiente mejor opción vigente.
- Los roles quedan como Administrador(a) y Encargado(a) de bodega.
- Donde decía «piloto» ahora dice «MVP», porque no hay negocio piloto.
- C1, C3, C4, C5, C7, C8 y C10 quedan aceptadas.
- La cantidad sugerida descuenta las órdenes en tránsito.
- Tres proveedores validados: Central Mayorista, Jumbo y Santa Isabel.
- La comparación exige al menos 2 precios vigentes.

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
| D9 | **Precios sólo por scraping** de páginas públicas permitidas por robots.txt; nunca por endpoints internos del sitio, aunque respondan | Acordado (Acta E1) |
| D10 | Autenticación con **JWT emitido por la propia API** y contraseñas con bcrypt (C8) | Acordado |

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
                              Central Mayorista · Jumbo · Santa Isabel
```

**Stack:**

| Capa | Tecnología | Estado |
|---|---|---|
| Frontend | React + TypeScript | Mockup listo (Sofi); código por empezar |
| API | FastAPI ≥0.115, Pydantic 2, Uvicorn | Implementado |
| Persistencia | SQLAlchemy 2, Alembic, PostgreSQL (probado en 14; README pide 17) | Implementado |
| Scraping | `httpx` + lectura del JSON-LD (`schema.org/Product`) de cada página; BeautifulSoup sólo como respaldo. Sin Playwright: los tres proveedores entregan el precio en el HTML | Fuentes validadas (§12); código pendiente |
| Autenticación | JWT emitido por la propia API, contraseñas con bcrypt | Acordado (C8); código pendiente |
| Calidad | pytest (unit, integración con PostgreSQL real, contrato API), ruff | Implementado |
| Despliegue | Vercel (front) · Render web service + PostgreSQL + cron job (back) | Pendiente |

## 3. Qué hay hoy en el repo

| Plan | Qué cubre | Estado |
|---|---|---|
| P1 | Insumo con stock inicial (MVP: un insumo crítico) | ✅ |
| P2 | Entradas, salidas, unidades base (`kg`/`g`, `L`/`mL`, `unidad`), reversas y correcciones | ✅ |
| P3 | Conteo físico, ajuste atómico, faltante («merma») y tasa por período | ✅ |
| P4 | Consumo semanal (últimos 28 días completos), proyección de agotamiento | ✅ |
| P5 | Stock seguro automático (1 semana) o manual, alerta, cantidad a reponer (objetivo 2 semanas) | ✅ (C1 y C5 la cambian, §4.3) |
| D1–D7 | Inmutabilidad, movimientos retroactivos, conteos como puntos de conciliación | ✅ |
| P6 | Fuentes de precios y adaptadores | Fuentes validadas; adaptadores ❌ |
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

| Tabla | Campos clave | Para qué (REQ) |
|---|---|---|
| `users` | id, email, password_hash, name, **role** (`ADMIN` = Administrador(a) \| `WAREHOUSE` = Encargado(a) de bodega), is_active | REQ-14. Agregar `created_by` a `movements` y `counts` (el mockup muestra el usuario en el historial) |
| `suppliers` | id, name, price_source_url, **lead_time_days** (días corridos), **shipping_cost_clp** (despacho fijo, ingresado a mano), adapter, is_active | REQ-13, REQ-17, C4 |
| `supplier_products` | id, supplier_id, item_id, name_on_site, url, **pack_quantity** (en unidad base), min_packs | P7: compara formatos completos (manga de 10 × 1 kg → 10) |
| `price_quotes` | id, supplier_product_id, run_id, pack_price_clp, **unit_price_clp** (por unidad base), available, **observed_at**, **valid_until** (= observed_at + 7 días) | REQ-07, REQ-08. Sólo precios scrapeados (sin `source` ni carga manual). Se guarda el histórico completo |
| `scrape_runs` | id, supplier_id, started_at, finished_at, status (`OK`\|`PARTIAL`\|`ERROR`), message | REQ-07: registro de fallos que muestra el mockup en *Proveedores* |
| `suggestions` | id, item_id, status (`PENDING`\|`APPROVED`\|`REJECTED`), required_qty, **in_transit_qty**, supplier_product_id, packs, purchased_qty, total_clp, next_best_total_clp, savings_clp, inputs_snapshot (JSON), created_at | REQ-09. Guarda los datos y precios usados, para explicar la sugerencia |
| `suggestion_revisions` | id, suggestion_id, changed_by, changed_at, before (JSON), after (JSON) | **Por decidir (C11)**: sólo si el administrador puede ajustar antes de aprobar |
| `orders` | id, suggestion_id, code (`OC-0012`), status (`APPROVED`\|`RECEIVED`), decided_by, decided_at, expected_at, received_at, receipt_movement_id | REQ-10, REQ-15. La recepción crea un `ENTRY` y lo enlaza (C3) |

### 4.3 Reglas de cálculo (van al DRS como *Reglas de negocio*)

**Vigentes en el código, sin cambios:**
- **Consumo semanal** = salidas de los últimos 28 días completos / 4 (días locales, `America/Santiago`; excluye el día en curso). Con < 7 días: *estimación preliminar*; sin un día completo: no se calcula. **Consumo diario** = consumo semanal / 7.
- **Stock seguro** = consumo semanal (automático) o el valor manual si existe.
- **Merma** = `max(teórico − físico, 0)` por conteo; **tasa** = faltantes del período / (stock inicial del período + entradas + ajustes positivos). Si el conteo es **mayor** al teórico, `needs_review = true` («Revisar» en el mockup, C10).

**Acordadas, por implementar:**

- **Tiempo de entrega considerado (L)** = el **mayor** `lead_time_days` entre los proveedores activos del insumo, en **días corridos** (C12).
- **Stock proyectado a la llegada** = `stock actual − consumo diario × L`.
- **Alerta de reposición (C1)** si `stock proyectado a la llegada ≤ stock seguro`. El borde es **«≤»**: con «<» la alerta saldría un día tarde y el pedido llegaría con menos que el stock seguro.
  Mientras exista una orden **aprobada y no recibida**, la alerta del insumo se muestra como **atendida (en tránsito)** y no genera otra sugerencia.
- **Cantidad sugerida (C5)** = `max(0, 2 × consumo semanal − stock proyectado a la llegada − en tránsito)`. *En tránsito* es la suma de las órdenes aprobadas y no recibidas del insumo. Se **redondea hacia arriba** a formatos completos del proveedor recomendado.
- **Precio comparable** = precio **normal publicado**, sin precios de socio, membresía ni tarjeta, **dividido por la cantidad del formato** (precio por kg, L o unidad). Así se comparan formatos distintos: manga de 10 × 1 kg contra bolsa de 1 kg.
- **Vigencia de un precio** = 7 días desde que se obtuvo.
  - Si un proveedor falla, se usa su **último precio vigente**, mostrando su fecha.
  - Si ya venció, ese proveedor **se excluye** de la comparación.
- **Comparación (REQ-08):**
  - Para cada proveedor con precio vigente, el costo total es `formatos × precio del formato + despacho fijo` (C4).
  - **Sólo se recomienda proveedor si hay al menos 2 precios vigentes.** Con menos, el sistema avisa que no puede comparar.
- **Ahorro (C6)** = total de la **siguiente mejor opción vigente** − total recomendado, en $ y en %.

**Ejemplo con los números del mockup** (para Sofi):

| Dato | Valor |
|---|---|
| Stock actual | 9 u |
| Consumo semanal | 7 u (1 u/día) |
| Stock seguro | 5 u |
| Tiempos de entrega | 2, 3 y 4 días → L = 4 |
| Stock proyectado a la llegada | 9 − 4 = **5 u** |
| Alerta | 5 ≤ 5 → «pide hoy» |
| Cantidad sugerida, sin nada en tránsito | 14 − 5 − 0 = **9 u** |
| Cantidad sugerida con una orden de 9 u en tránsito | **0** (alerta atendida) |

**El mockup muestra 20 u y «≈3,5 semanas»: hay que cambiarlo a 9 u.** Con 9 u, el pedido llega con 5 u en bodega y deja 14 u (dos semanas de consumo).

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

| Método y ruta | Qué hace | REQ | Nota |
|---|---|---|---|
| `GET /items` | **Listar insumos** | REQ-01 | El mockup lista insumos; hoy sólo existe `GET /items/{id}` |
| `PATCH /items/{id}` | Editar nombre (y proveedores asociados) | REQ-01 | Hoy sólo se puede editar el stock seguro |
| `POST /auth/login` · `GET /auth/me` | Sesión y usuario actual | REQ-14 | C8 |
| `GET/POST /suppliers` · `PATCH /suppliers/{id}` | Proveedores con URL, días de entrega y despacho fijo | REQ-13, REQ-17 | C4 |
| `GET /suppliers/{id}/scrape-runs` | Registro de fallos | REQ-07 | — |
| `GET /prices/{item_id}` | Ofertas por proveedor (precio por unidad, fecha, vigente/vencida), total para la cantidad requerida, mejor opción o aviso «no se puede comparar» | REQ-08 | Regla de ≥ 2 vigentes |
| `POST /prices/{item_id}/refresh` | Botón «Actualizar precios» (corre los adaptadores) | REQ-07 | — |
| `POST /suggestions` · `GET /suggestions/{id}` | Generar y ver la sugerencia | REQ-09 | `PATCH` sólo si entra C11 |
| `POST /suggestions/{id}/decision` | `{"decision": "APPROVE"\|"REJECT", "reason"}` → crea la orden; sólo Administrador(a) | REQ-10 | — |
| `GET /orders` · `POST /orders/{id}/receipt` | Historial; registrar recepción (crea `ENTRY`) | REQ-15 | C3 |
| `GET /metrics?start&end` | Alertas atendidas antes del quiebre, quiebres, merma, órdenes, ahorro | REQ-12 | C7 |

Ya no existe `POST /prices/{item_id}/manual` (C2).

**Ajuste a una respuesta existente:** `MovementResponse` debe traer `balance_after` y `created_by`. La tabla de *Movimientos* del mockup muestra el **stock después de cada movimiento** y el **usuario**, y el frontend no puede calcular bien el saldo cuando hay reversas o movimientos retroactivos (REQ-16).

### 5.4 Contrato de los adaptadores (Rodrigo)

`scraper/adaptadores/<proveedor>.py` implementa `fetch(url) -> RawQuote(name_on_site, price_text, pack_text, available, url)`.
- **Los tres proveedores publican el precio como JSON-LD** (`schema.org/Product` → `offers`). El adaptador lee eso, no clases CSS: es el formato que el sitio publica para buscadores y no cambia con el diseño.
- **Jumbo y Santa Isabel** usan la misma plataforma (VTEX) y comparten casi todo el adaptador.
- **Central Mayorista** trae además `priceValidUntil`, que se guarda como dato.
- La normalización (`13400` + «MANGAx10» + «1KG» → `pack_quantity = 10`, `unit_price = 1340 $/kg`) va en un único `normalize.py`.
- Un adaptador que falla lanza una excepción: la corrida queda `PARTIAL` y se registra en `scrape_runs`, sin botar a los demás. Un **404** se registra como «producto no encontrado» (hay que actualizar la URL), no como caída del sitio.
- **Prohibido:** usar endpoints internos (`/api/…`) aunque respondan, saltarse una protección anti-bots o un captcha, y consultar con menos de 2 s entre requests.

## 6. Entorno operativo (para el DRS §6)

- **Cliente:** navegador moderno (Chrome, Safari, Edge, Firefox; últimas 2 versiones), en computador o celular, responsive desde 360 px. Sin instalación.
- **Servidor:** nube. Render (Linux) para la API (Python 3.11+, FastAPI/Uvicorn), la base PostgreSQL y la tarea programada de precios. Vercel para el frontend (Node 20 sólo para compilar).
- **Conectividad:** requiere internet; todo el tráfico va por HTTPS.
- **Coexiste con:** los sitios públicos de los proveedores (fuente de precios). Sin integración con ERP, POS ni contabilidad en el MVP.
- **Datos:** no hay negocio piloto. El MVP se valida con precios reales de internet y con una base de movimientos realista generada por el equipo (dataset v1 de Rodrigo: 10 semanas).
- **Zona horaria:** configurable por insumo; el MVP usa `America/Santiago`.

## 7. Interfaces externas y requerimientos no funcionales (para el DRS §9–§10)

| Tipo | Contenido |
|---|---|
| Usuario | Web responsive en español: login, panel de stock, movimientos, conteo y merma, precios, órdenes, métricas, insumos, proveedores (el mockup de Sofi) |
| Hardware | Ninguna específica |
| Software | PostgreSQL; páginas públicas de producto de Central Mayorista, Jumbo y Santa Isabel |
| Comunicación | HTTPS/TLS; API REST JSON con token Bearer; contrato publicado en OpenAPI |

**No funcionales:**
- El saldo nunca es negativo, y lo garantiza la base (ya implementado).
- El historial es inmutable (ya implementado).
- Respaldo cada 24 h, con 7 copias y una restauración probada antes de la E3 (P10).
- Si un proveedor falla, la app sigue funcionando y usa su último precio mientras esté vigente.
- Sólo un(a) Administrador(a) aprueba órdenes.
- Las contraseñas se guardan con hash.
- El scraping respeta robots.txt y espera al menos 2 s entre requests.

## 8. Decisiones del equipo: repo vs. DRS vs. mockup

| # | Tema | Decisión | Estado |
|---|---|---|---|
| C1 | Alerta y tiempo de entrega | La alerta anticipa el tiempo de entrega (§4.3) | ✅ Aceptada |
| C2 | Carga manual de precios | **Se elimina**: el Acta de la E1 exige obtener los precios por scraping. Se mantiene la **vigencia de 7 días** | ✅ Decidida por el equipo |
| C3 | Recepción de la orden | Estados sugerida → aprobada → recibida; la recepción crea un `ENTRY` enlazado | ✅ Aceptada |
| C4 | Despacho | Costo fijo por proveedor, ingresado a mano | ✅ Aceptada |
| C5 | Cantidad sugerida | `2 × consumo semanal − stock proyectado a la llegada − en tránsito`, redondeada a formatos | ✅ Aceptada, con el descuento de lo en tránsito |
| C6 | Referencia del ahorro | Frente a la **siguiente mejor opción vigente** (como el mockup) | ✅ Decidida por el equipo |
| C7 | «Quiebres evitados» | «Alertas atendidas antes del quiebre» + quiebres del período | ✅ Aceptada |
| C8 | Autenticación | JWT en la propia API | ✅ Aceptada |
| C9 | Numeración de REQ | **17 REQ** (lista de Nati): la del DRS sin la carga manual y con las tres últimas corridas en una | ✅ Decidida por el equipo |
| C10 | Conteo mayor al teórico | `needs_review = difference > 0` | ✅ Aceptada |
| C11 | Ajustar cantidad o proveedor antes de aprobar (`suggestion_revisions`) | Recomiendo **dejarlo fuera del MVP**: el mockup no lo tiene y suma 6–10 HH. Si no le sirve la sugerencia, el administrador la rechaza con un motivo | ✅ Aceptada |
| C12 | Alerta: borde, días y proveedor | Borde **«≤»**, días **corridos** y el **mayor** tiempo de entrega entre los proveedores activos del insumo. Es lo que ya supone el mockup (Proveedor B, 4 días) | ✅ Corregido |
| C13 | Proveedores | Central Mayorista, Jumbo y Santa Isabel (§12) | ⏳ Por confirmar con el equipo |

**Numeración oficial (C9):**
- REQ-01 Registrar insumos · REQ-02 Registrar entradas y salidas · REQ-03 Registrar conteo físico
- REQ-04 Calcular consumo promedio · REQ-05 Proyectar quiebre de stock · REQ-06 Emitir alerta de reposición
- REQ-07 Obtener precios mediante Web Scraping · REQ-08 Comparar precios
- REQ-09 Generar orden de compra sugerida · REQ-10 Aprobar o rechazar la orden
- REQ-11 Calcular merma · REQ-12 Mostrar panel de métricas · REQ-13 Gestionar proveedores · REQ-14 Autenticar usuarios
- REQ-15 Registrar recepción y estado de la orden · REQ-16 Consultar historial de movimientos · REQ-17 Registrar tiempo de reposición

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
2. **Cubre ~40 % de los REQ.** Faltan REQ-07, 08, 09, 10, 12, 13, 14, 15 y 17 (precios, órdenes, auth, métricas, tiempo de reposición). La demo es el 17-10: hay que priorizar el **flujo vertical** (insumo → alerta → precios → sugerencia → aprobación) por sobre más casos borde.
3. **La alerta y la cantidad sugerida del código no son las acordadas** (§4.3). Hay que cambiarlas antes de que Sofi conecte el panel.
4. **Sobredimensionado para el MVP.** D5–D7 (movimientos retroactivos, conteos como puntos de conciliación, reinicio de observación) son correctos pero no los pide ningún REQ, y cada módulo nuevo (recepción de órdenes) tiene que respetarlos. **Congelar: no más reglas D** hasta terminar el flujo completo.
5. **Faltan dos rutas para el mockup:** `GET /items` (listar) y `PATCH /items/{id}` (editar). Además, el historial no trae `balance_after` ni el usuario (§5.3).
6. **No hay despliegue.** Falta `render.yaml`/Procfile, ejecutar las migraciones al arrancar y el origen CORS por variable. Sin esto no hay *link a la aplicación* para la rúbrica (*Evidencia de avance*, 2,5 pts).
7. **README orientado a Windows** (PowerShell). Agregar los comandos de macOS/Linux. Además pide PostgreSQL 17 cuando funciona con 14: bajar el requisito o fijar 16, que es lo que ofrece Render.
8. **`GET /alerts` recalcula el historial completo de cada insumo.** Con un insumo da igual; si el MVP crece, conviene cachear el estado.
9. **Uso de IA.** `AGENTS.md` muestra que el repo se construyó con un agente. El curso exige **declararlo** (§13).
10. **`AGENTS.md` y el plan nombran a Alvi** como proveedor inicial. Hay que actualizarlos a los proveedores validados (§12).

### 9.1 La recomendación de arquitectura que ya está en el repo (P10–P12)

| Recomendación del repo | ¿La sigo? | Comentario |
|---|---|---|
| Frontend React + TypeScript | ✅ | Igual |
| Backend Python + FastAPI | ✅ | Ya implementado |
| **PostgreSQL + autenticación mediante Supabase** | ⚠ Parcial | Cambio la parte de **auth** por JWT propio (C8). La base puede ser Supabase o Render indistintamente, porque el código sólo usa la URL `postgresql+psycopg://`. Ojo: el mismo plan (P10) advierte que **Supabase gratis no trae respaldos**, y además **pausa los proyectos inactivos** |
| Scraping `requests`/BeautifulSoup; Playwright sólo si hace falta | ✅ | Uso `httpx` y JSON-LD; Playwright no hace falta (§12) |
| Vercel (front) + Render/Railway (back) | ✅ | Elijo Render para tener API, base y *cron* en un solo lugar |
| API separada del scraping, con adaptadores por proveedor | ✅ | §2 y §5.4 |
| Sugerencia que guarda los datos y precios usados | ✅ | `suggestions.inputs_snapshot` (§4.2) |
| Contrato P11 (`/prices`, `/suggestions`, `/orders`, `/metrics`) | ✅ | Mantengo esos nombres y agrego lo que piden los REQ (§5.3) |
| Respaldo cada 24 h, 7 copias, restauración probada (P10) | ✅ | Paquete 8; propongo un *cron* de Render con `pg_dump` a almacenamiento privado |
| «Arquitectura simple y mantenible» (`AGENTS.md`) | ⚠ | El núcleo D5–D7 no es simple (punto 4 de §9) |

**Detalle menor del plan:** sus enlaces a las fuentes (`../Proyecto GPTI/Entrega 1/…`, `../../docs/decisions-and-questions.md`, `docs/course-guide.md`) apuntan a archivos que **no están en el repo** y salen rotos en GitHub.

## 10. Paquetes de trabajo técnicos (para la EDT de Rai)

La estimación de HH por tarea está en `E2 Planificacion/Estimacion HH (Pedro).md` (≈ 194 HH en total, 175 para la E2).

1. **Base existente:** inventario P1–P5 ✅ (sólo mantener).
2. **Ajustes al núcleo:** CORS, `GET/PATCH /items`, `balance_after`/`created_by`, alerta con tiempo de entrega y en tránsito (C1, C12), `needs_review` (C10).
3. **Autenticación y roles** (C8).
4. **Proveedores y precios:** tablas, CRUD de proveedores, validación de fuentes (§12), **tres adaptadores** (Central Mayorista, Jumbo, Santa Isabel), normalización, corrida programada, vigencia y registro de fallos.
5. **Comparación y sugerencia** (P7, C4–C6, regla de ≥ 2 vigentes).
6. **Decisión, órdenes y recepción** (P8, C3).
7. **Métricas** (P9, C7) — E3.
8. **Despliegue y respaldos** (P10, P12).
9. **Frontend** (Sofi): 9 pantallas del mockup + integración.
10. **Pruebas del Plan de Pruebas** (vinculadas a REQ-01…17) y **demo**.

## 11. Registro de Riesgos (Fran)

Fran ya aplicó la revisión de la v1.0:
- **R03** subió a probabilidad 0,8.
- **R04** tiene el plan nuevo: congelar el modelo y el contrato el 05-10.
- **R11** tiene la evidencia de robots.txt.
- Se agregaron **R13** (concentración del conocimiento técnico) y **R14** (límites del hosting gratuito), a mi cargo.

Queda por ajustar con la v1.1:

| Riesgo | Propuesta |
|---|---|
| **R02** Bloqueo de sitios | Plan: «Tres proveedores validados. Si uno falla, se usa su último precio mientras esté vigente (7 días). Si quedan menos de 2 precios vigentes, el sistema avisa que no puede comparar». Ya no hay carga manual de respaldo |
| **R02 / R11** | Alvi, Tottus, Unimarc, Prisa y la ficha de producto de Líder bloquean el acceso automatizado: quedan **descartados**, no «por validar» (§12) |
| **R13** Concentración del conocimiento | Respuesta: Rodrigo toma el pipeline de precios y la recepción de órdenes (ver la estimación de HH) |
| **Nuevo: dependencia de dos grupos** | Central Mayorista es de **Walmart**; Jumbo y Santa Isabel, de **Cencosud**. Si Cencosud bloquea, quedan menos de 2 proveedores. Respuesta: Super Líder (Walmart) como reserva técnica (§12) |

## 12. Proveedores validados (06-10)

Detalle y evidencia en `E2 Planificacion/Validacion scraping (Pedro).md` y `Evidencia scraping 2026-10-03/`.

| Proveedor | Grupo | robots.txt | Acceso | Azúcar Iansa 1 kg (05-10) | Veredicto |
|---|---|---|---|---|---|
| **Central Mayorista** | Walmart | Permite `/p/*` | ✅ JSON-LD | Manga 10 × 1 kg $13.400 → **$1.340/kg** | ✅ Proveedor 1 |
| **Jumbo** | Cencosud | Permite `/…/p` | ✅ JSON-LD | Bolsa 1 kg **$1.250** | ✅ Proveedor 2 |
| **Santa Isabel** | Cencosud | Permite `/…/p` | ✅ JSON-LD | Bolsa 1 kg **$1.530** | ✅ Proveedor 3 |
| Super Líder | Walmart | Permite `/v/` y `/ip/` | ⚠ La ficha `/ip/` redirige a `/blocked`; sólo la página de categoría `/v/` responde | Bolsa 1 kg $1.390 (en `/v/`) | Reserva técnica, no recomendada |
| Alvi | SMU | No se puede leer (403) | ❌ Akamai bloquea todo el sitio | — | ❌ Descartado |
| Tottus | Falabella | Permite | ❌ Cloudflare (403, «Just a moment») | — | ❌ Descartado |
| Prisa | independiente | — | ❌ Desafío JavaScript | — | ❌ Descartado |
| aCuenta | Walmart | Permite | ⚠ El producto se arma en el navegador | — | Sólo con Playwright |

**El tercer proveedor no puede ser de un grupo distinto:** todos los que no son Walmart ni Cencosud bloquean el acceso automatizado, y saltarse ese bloqueo contradice R11. Por eso los tres validados quedan en dos grupos.

## 13. Declaración de uso de IA (para los créditos de la E2)

| Qué | Asistente | Para qué | Cómo se validó |
|---|---|---|---|
| Código del backend (`AGENTS.md`) | **Por completar (Rodrigo)** | Implementar P1–P5 y D1–D7 | Por completar (Rodrigo): tests escritos, revisión del código |
| Este documento, la validación de proveedores y la estimación de HH | Claude (Claude Code, Anthropic) | Revisar el repo contra el DRS y el mockup, redactar el documento, probar el acceso a los sitios y armar la estimación | Pedro: corrió los tests (54 passed), comparó cada decisión con el DRS, el mockup y el Acta, verificó los precios en los sitios y revisó el texto |

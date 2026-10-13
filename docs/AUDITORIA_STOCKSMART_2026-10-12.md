# Auditoría, integración y verificación de StockSmart

Sesión del 12/10/2026, America/Santiago. Verificación final de ejecución: 13/10/2026 01:56 UTC. Repositorio: `Rvillena75/Proyecto-GPTI-grupo-4-back`. [PR #1](https://github.com/Rvillena75/Proyecto-GPTI-grupo-4-back/pull/1), abierto hacia `main`.

Resultado: módulos de Rodrigo integrados y funcionando en una API local con PostgreSQL aislado; rama publicada; 218 tests aprobados. El flujo completo sigue pendiente de las rutas de sugerencias, decisión y autenticación de Pedro. Render no verificable por falta de servicio/URL confirmados. El PR no se ha incorporado a `main`.

## 1. Diagnóstico inicial

- Directorio de trabajo: `Proyecto/`; checkout Git en `github/`. La carpeta `Proyecto GPTI/` contiene documentos académicos, requisitos, arquitectura y mockups; no se modificó su contenido ni se creó frontend.
- Rama inicial `main`, commit `48c789b`; seis archivos modificados, 78 archivos no rastreados y ningún cambio preparado para commit. Archivos modificados: `Arquitectura.md`, `PLAN_BACKEND_STOCKSMART.md`, `README.md`, `backend/app/services/inventory.py`, `backend/pyproject.toml`, `docs/decisions.md`. Los no rastreados contenían los módulos E2 de Rodrigo, scraper, pruebas y fixtures.
- Remoto inicial `https://github.com/Rvillena75/Proyecto-GPTI-grupo-4.git`, redirigido al repositorio actual con sufijo `-back`. Se actualizó `origin` a la dirección canónica después de verificarla mediante GitHub.
- Tras `git fetch origin`: cero commits exclusivamente locales y cuatro exclusivamente remotos, todos de Pedro: `fe99a91` arquitectura v1.2; `71d9b79` modelo y endpoints de integración; `925eaea` CORS; `81b3a31` Blueprint de Render. Solo existía la rama remota `main`; no había PR abiertos ni ejecuciones GitHub Actions.
- Stack comprobado: backend Python/FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, psycopg 3, PostgreSQL y Uvicorn; httpx para proveedores públicos. Este checkout no contiene frontend. Pruebas ejecutadas con Python 3.14.3 y PostgreSQL 18; Render configura Python 3.12.7 y PostgreSQL 17, sin poder verificar ese runtime desplegado.
- Se leyeron instrucciones `AGENTS.md`, plan funcional, arquitectura, decisiones, guía E2, código, migraciones, pruebas y commits. La declaración de IA documenta cambios de Claude; no se consultaron conversaciones externas.
- Riesgos concretos: archivos simultáneamente editados; puente de recepción no conectado al ORM; precios solo en archivos; campos nuevos de `StockAssessment` incompatibles con el wrapper E2; README con estados históricos presentados como actuales; credenciales incrustadas por el proveedor en HTML público.
- Puerto 8000 inicialmente sin listener; solicitud `/health` rechazada por conexión. No había `STOCKSMART_DATABASE_URL` ni URL de tests en el entorno. Se dejó intacto el PostgreSQL existente del puerto 5432.

## 2. Cambios realizados

### Preservación e integración

- Respaldo previo de 114 archivos, incluyendo todos los no rastreados: ZIP, manifest SHA-256, parches binarios y bundle Git. Ubicación local: `Proyecto/.stocksmart-audit/20261012-221102/`.
- Conservación recuperable mediante `9562d3f` (trabajo E2 original), `530591a` (merge con los cuatro commits de Pedro) y `dda464e` (implementación integrada). Estos commits permanecen en `recovery/stocksmart-20261012-original`, **solo local**.
- Conflicto real en `Arquitectura.md`, sección de declaración de IA: se conservaron la declaración de Rodrigo, las dos filas añadidas por Pedro y la distinción entre validación técnica y aceptación humana.
- Corrección de argumentos duplicados `lead_time_days`/`in_transit_quantity` en `app/domain/replenishment.py`. Se conservaron los fixtures y reglas históricas E2; la API utiliza el evaluador y contexto ORM de Pedro.
- La primera publicación fue rechazada por GitHub Push Protection, código GH013, por cadenas AWS dentro de HTML de Central. El escaneo inicial no las había detectado. Se preparó una nueva rama desde `origin/main`, conservando la rama de recuperación y sin reescribir historial remoto ni eludir la protección.
- Publicación de tres HTML redactados y sus tres sidecars de procedencia. 43 de los 49 fixtures permanecen idénticos byte a byte; los seis cambios corresponden exclusivamente a esos HTML y metadatos. Los 49 originales están intactos en el respaldo y la rama de recuperación. El parser se comparó antes/después y produce exactamente el mismo `RawQuote`; fechas, precios y datasets permanecen iguales.
- Los metadatos mantienen `sha256` del HTTP original y agregan `artifact_sha256` y descripción de la redacción. Replay verifica los bytes publicables y conserva la identidad de los precios originales. La segunda publicación fue aceptada.

### Funcionalidades y archivos

| Archivos | Cambio |
|---|---|
| `backend/app/api/procurement_schemas.py`, `procurement_routes.py`, `app/main.py` | Contrato REST de proveedores/productos, precios, corridas, historial y recepción, conectado al mismo backend. |
| `backend/app/services/catalog.py` | Crear/listar/editar/desactivar proveedores y productos; validación de referencias, URLs, formatos, precisión y duplicados; protección de identidad ya usada. |
| `backend/app/services/prices.py` | Persistencia en `price_quotes`/`scrape_runs`, vigencia original, fallos aislados, disponibilidad, concurrencia y comparación de ofertas. |
| `backend/app/services/orders.py` | `GET /orders` sobre datos persistidos; recepción completa y atómica, reintentos, peso variable y retiro del tránsito. |
| `backend/app/services/inventory.py` | Conservación del join transaccional de Rodrigo con las ampliaciones de Pedro; protección de movimientos enlazados a recepción. |
| `backend/app/persistence/models.py`, `migrations/versions/0004_scrape_config.py` | Una columna JSONB opcional por producto para los parámetros existentes del normalizador; migración aditiva. |
| `backend/scraper/normalize.py`, `pipeline.py`, `store.py`, `runtime.py`, `postgres.py` | Normalizador/adaptadores reutilizados; comparación por producto y proveedores distintos; worker PostgreSQL; bloqueo y pausas compartidos; replay de evidencia redactada. |
| `backend/tests/api/test_procurement.py`, `unit/test_procurement_comparison.py`, `unit/test_fixture_redaction.py` | Regresiones de API, PostgreSQL, concurrencia, rollback, precios y publicación segura. |
| `backend/tests/api/test_contract.py`, `integration/test_inventory_postgres.py` | Contrato ampliado y revisión esperada de Alembic, manteniendo las invariantes existentes. |
| `.gitattributes`, `.gitignore` | Bytes de fixtures estables; exclusión de outputs, dumps, bundles, ZIP y datos PostgreSQL, además de secretos/caches ya excluidos. |
| `README.md`, `PLAN_BACKEND_STOCKSMART.md`, `Arquitectura.md`, `docs/decisions.md`, `E2_Rodrigo.md`, `INTEGRACION_BACKEND.md` | Estado vigente, contratos, comandos Windows/bash, pendientes y límites del flujo. |

`render.yaml`, configuración de conexión/CORS, `forecast.py`, rutas y esquemas originales de inventario de Pedro coinciden con `origin/main`. No se añadieron tablas de órdenes/precios duplicadas ni métricas E3. No se eliminó trabajo de terceros.

Commits publicados de implementación: `fd4fd9c` integración y evidencia publicable; `ec3cb14` ajuste de comentario para lint. Este informe se agrega mediante un commit documental separado.

## 3. Estado de las funcionalidades

Estado inicial resume lo encontrado tanto en el checkout como en GitHub.

| Funcionalidad | Responsable | Estado inicial | Estado final | Observaciones |
|---|---|---|---|---|
| Proveedores | Rodrigo | Tabla remota; sin API | Implementado y probado | Crear/listar/editar/desactivar; plazos y URLs validados; conserva historial. |
| Productos | Rodrigo; núcleo compartido | Insumos locales; listado/edición y vínculos remotos | Integrado y probado | GET/POST/PATCH de insumos y productos por proveedor; unidad e historia protegidas. |
| Precios | Rodrigo | Scraper/JSON local; tablas remotas sin conexión | Persistido y operativo | GET/refresh/worker, siete días, fallos aislados, formatos y ≥2 proveedores; tres fuentes reales OK. |
| Sugerencias | Pedro | Tabla y comparación base, sin rutas | Pendiente de Pedro | No existen POST /suggestions ni GET /suggestions/{id}; no se reimplementó ese módulo. |
| Aprobación/rechazo | Pedro | Estados ORM, sin endpoint de decisión | Pendiente de Pedro | La recepción exige orden y sugerencia aprobadas persistidas; fixtures SQL para verificar la interfaz. |
| Recepción | Rodrigo | Puente E2 probado, sin API/ORM de órdenes | Implementado y probado | ENTRY/stock/RECEIVED en una transacción; concurrencia, rollback, reintentos y peso real. |
| Autenticación | Pedro | Tabla users; sin JWT/rutas | Pendiente de Pedro | /auth/me devuelve 404; OpenAPI sin securitySchemes; rutas actualmente sin autorización. |
| GET /orders | Rodrigo | Ausente | Implementado y probado | Datos persistidos, enlace a sugerencia y recepción; sin filtros/paginación inventados. |
| Render | Pedro | Blueprint, sin URL/servicio confirmados | No verificable | Configuración preservada; usuario indicó que asume que todavía no existe despliegue. |

## 4. Estado de la API

### Local: funcionando

- API: `http://127.0.0.1:8000`; documentación `/docs`; contrato `/openapi.json`.
- PostgreSQL exclusivo de auditoría: `127.0.0.1:55432`, base API `stocksmart_audit_api`; suites en `stocksmart_audit_test`; migración con datos en `stocksmart_upgrade_test`.
- Listener final: API PID 10056, PostgreSQL PID 6304. El servidor se dejó levantado; son procesos locales de esta auditoría, sin servicio instalado.
- Arranque real desde `backend/`: `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`, con las variables de conexión y CORS configuradas solo para esta instancia.
- El primer intento de lanzar el servidor en segundo plano fue rechazado automáticamente; se levantó mediante una sesión de ejecución. El proceso Uvicorn fue reiniciado al finalizar para cargar la versión publicable y verificar persistencia.

| Solicitud HTTP real | Resultado |
|---|---|
| GET /health | 200, `{"status":"ok"}`, PostgreSQL accesible. |
| GET /openapi.json | 200; 24 paths; sin /metrics ni esquemas de autenticación. |
| GET /items, /suppliers, /orders | 200; inicialmente listas vacías en una base nueva. |
| POST /suppliers y /suppliers/{id}/products | 201 para los tres proveedores/productos de la fixture. |
| GET /prices/1 | 200; ofertas persistidas, con fecha y vigencia. |
| POST /prices/1/refresh | 200; Central, Jumbo y Santa Isabel en OK/CURRENT con consulta pública real. |
| POST /orders/1/receipt y reintento | 200 ambos; mismo movimiento 30, una sola entrada. |
| GET /orders y /items/1 tras reiniciar | 200; orden RECEIVED y saldo 5.250000 kg, tránsito 0. |
| POST /movements/30/reverse | 409: recepción enlazada protegida. |
| GET /prices/9999 | 404: insumo inexistente, error esperado. |
| POST /prices/1/refresh con worker ya bloqueado | 409 PRICE_WORKER_BUSY, sin consultar proveedores. |
| OPTIONS /orders desde el origen Vercel | 200; Access-Control-Allow-Origin coincide. |
| GET /auth/me y /suggestions/1 | 404: rutas pendientes, no errores de autenticación ni caída del servidor. |

Se ejecutaron 19 solicitudes del flujo de integración, además de arranque, CORS y comprobaciones finales. Las aprobaciones se prepararon como fixtures SQL en la base aislada: **no se verificó generación/aprobación HTTP ni un flujo autenticado**. No se escribieron datos reales ni producción.

Evidencia local en el respaldo: `http-startup.json`, `http-flow.json`, `http-cors.json`, `http-final.json`, `runtime.json`, script `http_probe.py` y dump `audit-api.dump`. El dump preserva la base de demostración.

### Render: no verificable

- `render.yaml`: servicio previsto `stocksmart-api`, rootDir `backend`, build `pip install .`, arranque `alembic upgrade head && uvicorn ... --port $PORT`, health `/health`; base prevista `stocksmart-db`. Puerto efectivo de Render no identificado: usa `$PORT`.
- No hay URL pública de backend documentada ni despliegues GitHub registrados. No hay acceso a logs/servicio Render; no se puede vincular una versión desplegada a un commit.
- Vercel `https://stocksmart-nine.vercel.app` responde 200; su bundle público contiene `http://127.0.0.1:8000` como dirección de API. No se modificó ese frontend.
- Se probó `https://stocksmart-api.onrender.com/health` como candidato inferido: primero timeout, luego 404 HTML `Cannot GET /health`, incompatible con la respuesta FastAPI del proyecto. **No está confirmado que ese host pertenezca a StockSmart**; no demuestra una caída ni es la URL identificada de la API.
- El usuario respondió que asume que todavía no hay despliegue. Esto se registra como contexto, sin convertir la hipótesis en un servicio inexistente comprobado.

## 5. Pruebas ejecutadas

Comandos desde `backend/`, usando `.venv/Scripts` en Windows y las bases aisladas del puerto 55432:

| Comando/verificación | Resultado real |
|---|---|
| `python -m pip install -e '.[test]'` | Instalación editable correcta. |
| `python -m pip check` | No broken requirements found. |
| `python -m pytest -q` sobre el trabajo original | 173 passed. |
| Tests de integración tras merge, antes del fix | 3 failed / 10 passed: argumentos duplicados en wrapper E2. Corregido. |
| `python -m pytest -q` después de integrar Pedro | 187 passed. |
| Suite durante nuevas funcionalidades | Se corrigieron errores del harness UTF-8, fixture sin reposición y comparaciones de timestamp/Decimal; regresiones finales aprobadas. |
| `python -m pytest -q --tb=short` final | **218 passed**, una advertencia de deprecación Starlette/httpx; sin tests omitidos. |
| `ruff check app scraper tests migrations` | All checks passed; observaciones de longitud/imports corregidas. |
| `ruff format --check app scraper tests migrations` | 72 files already formatted. |
| `python -m compileall -q app scraper` | Imports/sintaxis comprobados sin errores; arranque y suite también ejercitan imports. |
| `alembic -c alembic.ini upgrade head` | Migraciones 0001–0004 desde vacío. |
| Upgrade 0003 → 0004 sobre datos existentes | Insumo, proveedor, producto y precio conservados en todos sus campos; nueva columna NULL. |
| `alembic -c alembic.ini check` | No new upgrade operations detected, en ambas bases de validación. |
| `python -m scraper.postgres --help` | CLI disponible; misma frontera de persistencia que la API. |
| HTTP de red y scraping real | Códigos/resultados de la sección 4; tres adaptadores OK. |
| Hashes/seguridad de fixtures | Originales respaldados; tres derivados comerciales equivalentes; hashes de artifacts y replay válidos. Push Protection aceptó la publicación corregida. |
| `git diff --check`, `git diff --cached --check`, `git bundle verify` | Sin errores; bundle con historial completo. |

Cobertura: todas las regresiones anteriores de inventario; transacciones, triggers, conciliaciones, APIs y replay E2; 31 pruebas adicionales frente a las 187 integradas. Se prueban referencias inválidas, duplicados, cantidades/rango/precisión, metadata ausente, estados temporales, proveedores desactivados, disponibilidad, expiración, cambios concurrentes de configuración, costos independientes, peso variable, rechazo, rollback y recepción simultánea.

No ejecutable: autenticación, generación/decisión HTTP y pruebas del servicio Render, porque faltan implementación o acceso/URL. No existe CI GitHub en el repositorio; `statusCheckRollup` vacío no se presenta como una suite remota exitosa.

## 6. Estado de sincronización

- Rama local final: `integration/stocksmart-20261012`, publicada en `origin/integration/stocksmart-20261012`; revisión completa entregada mediante [PR #1](https://github.com/Rvillena75/Proyecto-GPTI-grupo-4-back/pull/1).
- `main` local se actualizó mediante `git merge --ff-only origin/main` a `81b3a31`, sin push a la rama compartida. Conserva exactamente los cuatro commits de Pedro. La integración desciende de ese commit.
- PR abierto, no draft, GitHub indicó `MERGEABLE`. Incorporarlo a `main` sigue pendiente. Los commits de implementación son `fd4fd9c` y `ec3cb14`, más el commit documental de este informe.
- Sin conflictos Git pendientes. Sin modificaciones accidentales ni artefactos generados rastreados; secretos/caches/outputs/bases/backups fuera de los commits.
- Rama original recuperable `recovery/stocksmart-20261012-original`, con `9562d3f`, `530591a` y `dda464e`, conservada localmente y excluida de la publicación. No publicar esa rama ni sus bundles: contienen los HTML originales del proveedor que activaron Push Protection.
- Evidencia de preservación: ZIP con los 114 archivos iniciales y sus hashes; 49 fixtures originales completos; parámetros/datasets económicos sin cambios; cada commit remoto como antecesor; ambos conjuntos de declaraciones IA; bundle final verificado y dump de la base de demostración.
- Se corrigió el bloqueo de publicación mediante copias redactadas y una nueva historia publicable desde `origin/main`. Nunca se usó reset hard, clean, force push ni bypass de seguridad.
- La rama de integración está sincronizada con su remoto; **el código nuevo todavía no está integrado en main ni desplegado en Render**.

## 7. Próximos pasos

1. **Equipo/Rodrigo:** revisar e incorporar el PR, incluyendo contrato y migración opcional; confirmar aceptación humana. Las funcionalidades de Rodrigo están implementadas y verificadas en la rama.
2. **Pedro:** implementar las rutas de sugerencia y decisión sobre el modelo conservado; generar órdenes con producto, formatos y cantidad aprobados. Las interfaces de recepción/historial están listas y probadas mediante fixtures.
3. **Pedro:** conectar JWT y roles a los endpoints existentes; aprobación exclusiva ADMIN y operaciones de bodega según el plan. Es necesario antes del uso con datos operacionales.
4. **Pedro:** crear/verificar el servicio y base Render, configurar variables/worker diario y comprobar su URL, salud y versión; entregar esa URL para que frontend deje de apuntar a localhost.
5. **Rodrigo/equipo:** cargar parámetros operacionales reales de proveedores/productos, distintos de plazos y consumos sintéticos de las fixtures, y ejecutar el flujo HTTP completo cuando existan sugerencia/decisión/auth.

Métricas/simulación E3 siguen postergadas; no se desarrollaron en esta intervención. El puente E2 de lotes/vida útil permanece disponible, pero no se convierten sus campos en obligatorios ni se inventa una devolución/conciliación de órdenes fuera del contrato actual.

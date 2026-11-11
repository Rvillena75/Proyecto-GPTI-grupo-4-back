# Cierre técnico del backend StockSmart — Entrega 2

Fecha de verificación: 2026-10-18T20:05:40.352171-03:00. Rama: `fix/backend-e2-audit-completion`. Base: `5333503b530251e945427b864f08b0ab7db1d300`.

## Resultado y límites

El backend permite el recorrido autenticado inventario → precios → sugerencia persistida → decisión humana → orden interna → recepción atómica. La validación actual ejecutó **357 tests en PostgreSQL 17 y 357 en PostgreSQL 18**, sin fallos, errores ni omisiones. Conserva los 220 casos originales; una expectativa de proyección fue corregida contra RN-07 y su test fue renombrado. Se revisó el diff completo y los nuevos componentes; esta segunda lectura técnica la realizó el mismo asistente, sin atribuirla a un revisor humano independiente.

De 17 requisitos funcionales del DRS evaluados exclusivamente como backend: **16 IMPLEMENTADO Y VERIFICADO** localmente y **1 PARCIALMENTE IMPLEMENTADO** (REQ-07: activación/ejecución automática remota sin acreditar). No se asigna una nota de rúbrica ni se calcula un porcentaje ponderado. La interfaz y la integración visual están fuera del alcance. Las metas 30/20/5 son resultados de negocio pendientes y no criterios satisfechos por estos tests.

No quedan bloqueantes P0/P1 conocidos **en el código revisado**. De los 22 hallazgos originales, los estados finales son {'IMPLEMENTADO Y VERIFICADO': 17, 'PARCIALMENTE IMPLEMENTADO': 5}. Los hallazgos aún no cerrados por prioridad original son {'P0': 0, 'P1': 3, 'P2': 2, 'P3': 0}. Los parciales F06/F07/F17/F18/F19 mantienen la prioridad original y están explicados abajo: cron/continuidad/almacenamiento remotos, ensamble de presentación/video y protocolo de E3. No se declaran resueltos por escribir configuración. El estado de la CI posterior a la publicación se conserva en [CI_RESULTADO.json](CI_RESULTADO.json).

El código está preparado para una etapa de despliegue autorizada. **El funcionamiento real en Render permanece NO VERIFICADO.** No se aplicó el Blueprint, no se compraron planes, no se ejecutó un despliegue manual y no se modificaron datos operacionales. La publicación se limita a la rama de corrección/PR; no incluye merge.

## Fuentes y jerarquía

Se conserva la jerarquía de enunciado/rúbrica, DRS, decisiones aprobadas y contratos, auditoría y código. Fuentes originales bajo `Proyecto/Proyecto GPTI/Entrega 2`: DRS `DOCUMENTO DE REQUERIMIENTOS/2.1 Documento de requerimientos de software.docx` §7 (REQ-01–17), §8 (RN-01–18), §10 (RNF); `Arquitectura.md` v1.2; `2.4 Plan de Pruebas.xlsx`; `Estimacion HH v2 (Pedro).pdf`; rúbrica pp.5,7–8. La auditoría de referencia está en `Proyecto/.stocksmart-audit/20261017-backend-e2-163745/`. Los hashes de 22 fuentes oficiales originales se comprobaron y permanecen iguales. No se publicó ese corpus ni se alteró el DRS para justificar el código.

La decisión histórica que recortaba P a cero se corrigió: una proyección puede representar demanda sin cubrir y ser negativa; el stock físico continúa protegido. Se preservan los parámetros, semillas y resultado del Dataset v2: SHA-256 `124a2586ff809c76084ff95cd6007c7119111ff52f0ab223c5237fe74f4958c1`. Sigue siendo evidencia sintética E2 y no un piloto real ni simulación comparativa E3.

## Inventario y trazabilidad de los 17 requisitos

La [matriz CSV completa](MATRIZ_TRAZABILIDAD_FINAL.csv) también contiene las RN, RNF y verificaciones operativas, con fuente, estado anterior, expectativa, evidencia nueva y brecha. Cada etiqueta VERIFICADO tiene el alcance local declarado.

| ID | Requisito | Estado final | Evidencia y alcance |
|---|---|---|---|
| REQ-01 | Registrar y editar insumos | IMPLEMENTADO Y VERIFICADO | backend/app/services/inventory.py:138; backend/tests/api/test_contract.py; 357/357 en PG17 y PG18. Crear/editar insumos con permisos, unidades y observación inicial controladas. |
| REQ-02 | Entradas y salidas | IMPLEMENTADO Y VERIFICADO | backend/app/services/inventory.py:190; backend/tests/integration/test_inventory_postgres.py; 357/357 en PG17 y PG18. Movimientos atómicos, autor y saldo actual/histórico no negativo. |
| REQ-03 | Conteo físico y ajuste | IMPLEMENTADO Y VERIFICADO | backend/app/services/inventory.py:367; backend/tests/integration/test_inventory_postgres.py; 357/357 en PG17 y PG18. Conteo y ajuste juntos; saldo final físico y rollback comprobados. |
| REQ-04 | Consumo promedio | IMPLEMENTADO Y VERIFICADO | backend/app/domain/forecast.py:44; backend/tests/unit/test_forecast.py; 357/357 en PG17 y PG18. Días completos locales, DST, sin historia y exclusión de invalidados. |
| REQ-05 | Fecha de quiebre | IMPLEMENTADO Y VERIFICADO | backend/app/domain/forecast.py:79; backend/tests/unit/test_forecast.py; 357/357 en PG17 y PG18. Proyección, cero consumo, historia insuficiente y horizonte fuera de rango. |
| REQ-06 | Alerta anticipada de reposición | IMPLEMENTADO Y VERIFICADO | backend/app/domain/forecast.py:79; backend/tests/api/test_rules_health_metrics.py; 357/357 en PG17 y PG18. P sin recorte; S=1,W=7,L=4 produce P=-3,Q=17; veto de acción en tránsito. |
| REQ-07 | Obtener precios por scraping | PARCIALMENTE IMPLEMENTADO | backend/app/services/prices.py:243; backend/tests/api/test_operations.py; 357/357 en PG17 y PG18. Parser aislado, fallback/TTL, tres fuentes reales y worker coordinado; programación diaria remota pendiente. |
| REQ-08 | Comparar precios y formatos | IMPLEMENTADO Y VERIFICADO | backend/app/services/prices.py:86; backend/tests/api/test_procurement.py; 357/357 en PG17 y PG18. Formatos completos, mínimos, dos proveedores distintos, costos/ahorro y veto en tránsito. |
| REQ-09 | Generar orden sugerida | IMPLEMENTADO Y VERIFICADO | backend/app/services/suggestions.py:53; backend/tests/api/test_e2_completion.py; 357/357 en PG17 y PG18. Snapshot PENDING durable; POST/GET consistente, no stock/orden y una pendiente ante carreras. |
| REQ-10 | Aprobar o rechazar | IMPLEMENTADO Y VERIFICADO | backend/app/services/suggestions.py:136; backend/tests/api/test_e2_completion.py; 357/357 en PG17 y PG18. Decisión ADMIN, motivo de rechazo, actor/fecha, idempotencia y aprobación/OC atómicas. |
| REQ-11 | Merma y revisión de excedentes | IMPLEMENTADO Y VERIFICADO | backend/app/services/inventory.py:526; backend/tests/api/test_rules_health_metrics.py; 357/357 en PG17 y PG18. Tasa RN-16 y needs_review en creación/historial; no interpreta faltante como desperdicio causal. |
| REQ-12 | Métricas por período | IMPLEMENTADO Y VERIFICADO | backend/app/services/metrics.py:15; backend/tests/api/test_rules_health_metrics.py; 357/357 en PG17 y PG18. API por período con indicadores observados e ids; línea base/impacto E3 no verificables. |
| REQ-13 | Proveedores y asociaciones | IMPLEMENTADO Y VERIFICADO | backend/app/services/catalog.py:145; backend/tests/api/test_e2_completion.py; 357/357 en PG17 y PG18. Gestión y sustitución activa de producto bajo la misma URL, conservando historia. |
| REQ-14 | Autenticación y roles | IMPLEMENTADO Y VERIFICADO | backend/app/services/auth.py:89; backend/tests/api/test_auth_security.py; 357/357 en PG17 y PG18. bcrypt, JWT temporal, usuario activo/rol actual, 401/403 y throttling sin cuentas predeterminadas. |
| REQ-15 | Estado y recepción de orden | IMPLEMENTADO Y VERIFICADO | backend/app/services/orders.py:54; backend/tests/api/test_e2_completion.py; 357/357 en PG17 y PG18. Sugerencia→aprobación→recepción por HTTP, ENTRY único y checks de estados completos. |
| REQ-16 | Historial de movimientos | IMPLEMENTADO Y VERIFICADO | backend/app/services/inventory.py:673; backend/tests/api/test_auth_security.py; 357/357 en PG17 y PG18. Historial inmutable con saldos y autor HTTP; legado/sintético sin autor inventado. |
| REQ-17 | Tiempo de reposición | IMPLEMENTADO Y VERIFICADO | backend/app/services/inventory.py:505; backend/tests/api/test_integration_1.py; 357/357 en PG17 y PG18. Plazo entero no negativo, máximo entre asociaciones activas y días corridos. |

## Cambios por componente y revisión

Autenticación propia con JWT HS256 de vida corta, emisor/audiencia/claims obligatorios, bcrypt coste 12, usuario activo y rol consultados en la base para cada petición. No hay registro público, cuenta predeterminada ni token permanente. Todas las rutas de negocio requieren identidad; ADMIN configura catálogo/insumos, genera y decide; WAREHOUSE registra movimientos, conteos y recepciones. Las contraseñas se aprovisionan por consola privada sin argv ni logs. La limitación por identidad de login devuelve 429. El cambio de contraseña por sí solo no revoca un token anterior: documentado TTL, desactivación de usuario o rotación de firma para revocación.

Sugerencias con snapshot de inventario, consumo, L/T, productos, quotes/fechas y costos completos. Una PENDING por insumo; se valida nuevamente al aprobar. Una decisión idéntica conserva actor/fecha originales; una contraria devuelve conflicto. Rechazo exige motivo no blanco. Aprobar crea exactamente una OC interna y no suma stock ni compra externamente. La recepción completa la cadena por HTTP, registra actor y una ENTRY; mantiene peso real para formatos variables y formato histórico en órdenes antiguas.

Fórmula única RN-07: `P=S-(W/7)*L`, `Q=max(0,2W-P-T)`. Para S=1,W=7,L=4,T=0: P=-3 y Q=17. Con tránsito no hay nueva acción de compra aunque se conserve la necesidad teórica explicativa. Se probaron fronteras, plazos, DST e historia insuficiente.

Scraping: se validan tipos JSON-LD; un fallo de fuente/archivo no omite proveedores independientes ni deja éxito falso. Una caída de BD se trata como infraestructura. Exclusión y pacing están en PostgreSQL antes del HTTP para API/cron con filesystems separados; se probaron crash/liberación y corridas abandonadas. Se conserva vigencia original y disponibilidad negativa. Evidencia comercial mínima/hash en PostgreSQL; HTML completo temporal se limpia y no se promete reconstruirlo desde un hash.

Persistencia: migración aditiva `0005_e2_completion`, índice parcial de producto activo por URL y de sugerencia PENDING por insumo, auditoría de decisiones, evidencia y checks de recepción completos. Upgrade de 0004 poblado conserva datos; estados corruptos detienen la migración sin reconciliación silenciosa. Movimientos/conteos históricos se mantienen inmutables.

Readiness comprueba revisión y columnas mínimas; conexión/consulta/locks tienen límites. Errores HTTP, validación, dominio, infraestructura e inesperados usan JSON sanitizado y correlación; health conserva su contrato propio. El pool de autenticación se libera antes de la transacción de negocio. CORS con orígenes explícitos y Bearer, sin cookies.

Métricas: lectura consistente por período `[start,end)`, merma por insumo/unidad, transiciones positivas→cero observadas, aprobaciones previas al depletion_at congelado y ahorro potencial frente al siguiente mejor costo. Devuelve ids/definiciones/null cuando faltan datos. No observa demanda perdida no registrada ni demuestra quiebres evitados. El equipo debe aprobar el protocolo y la línea base de E3.

Backups: pg_dump real, hash/manifiesto, publicación atómica de copia diaria, retención de siete fechas y restore en otra base vacía `_test`; se comprobaron datos, bcrypt y ambos roles. Nueve fechas simuladas no son nueve días transcurridos. S3 exige HTTPS/bloqueo público/cifrado, paginación y propagación de fallos; probado con mocks, sin bucket real. El Dockerfile usa cliente PostgreSQL 18 y usuario sin root.

Rendimiento: 19→16 consultas para tres insumos, sin retirar bloqueos de snapshot. En el ensayo HTTP con historia E2: 28 lecturas/14 clientes concurrentes, máximo 210.585 ms en ese laboratorio. No constituye un SLA ni garantiza esa latencia en Render.

| Requisito | Ubicación de código | Archivo de pruebas |
|---|---|---|
| REQ-01 | `backend/app/services/inventory.py:138` | `backend/tests/api/test_contract.py` |
| REQ-02 | `backend/app/services/inventory.py:190` | `backend/tests/integration/test_inventory_postgres.py` |
| REQ-03 | `backend/app/services/inventory.py:367` | `backend/tests/integration/test_inventory_postgres.py` |
| REQ-04 | `backend/app/domain/forecast.py:44` | `backend/tests/unit/test_forecast.py` |
| REQ-05 | `backend/app/domain/forecast.py:79` | `backend/tests/unit/test_forecast.py` |
| REQ-06 | `backend/app/domain/forecast.py:79` | `backend/tests/api/test_rules_health_metrics.py` |
| REQ-07 | `backend/app/services/prices.py:243` | `backend/tests/api/test_operations.py` |
| REQ-08 | `backend/app/services/prices.py:86` | `backend/tests/api/test_procurement.py` |
| REQ-09 | `backend/app/services/suggestions.py:53` | `backend/tests/api/test_e2_completion.py` |
| REQ-10 | `backend/app/services/suggestions.py:136` | `backend/tests/api/test_e2_completion.py` |
| REQ-11 | `backend/app/services/inventory.py:526` | `backend/tests/api/test_rules_health_metrics.py` |
| REQ-12 | `backend/app/services/metrics.py:15` | `backend/tests/api/test_rules_health_metrics.py` |
| REQ-13 | `backend/app/services/catalog.py:145` | `backend/tests/api/test_e2_completion.py` |
| REQ-14 | `backend/app/services/auth.py:89` | `backend/tests/api/test_auth_security.py` |
| REQ-15 | `backend/app/services/orders.py:54` | `backend/tests/api/test_e2_completion.py` |
| REQ-16 | `backend/app/services/inventory.py:673` | `backend/tests/api/test_auth_security.py` |
| REQ-17 | `backend/app/services/inventory.py:505` | `backend/tests/api/test_integration_1.py` |

Los archivos del backend verificado y sus hashes están en VERIFICACION.json. Se revisaron los cambios en rutas, schemas, modelos, migración, parser/transporte/coordinación, CLI, Dockerfile, locks, workflow, tests y documentación; no se añadieron cambios de frontend.

## Hallazgos originales

| ID | Prioridad original | Hallazgo | Estado final | Tarea |
|---|---|---|---|---|
| F01 | P0 | La API permite escritura anónima y omite roles y autoría | IMPLEMENTADO Y VERIFICADO | T02 |
| F02 | P1 | No se generan ni consultan sugerencias persistidas | IMPLEMENTADO Y VERIFICADO | T05 |
| F03 | P1 | No hay decisión humana HTTP ni persistencia completa del rechazo | IMPLEMENTADO Y VERIFICADO | T06 |
| F04 | P1 | Un JSON-LD inesperado aborta todos los proveedores y deja la corrida abierta | IMPLEMENTADO Y VERIFICADO | T03 |
| F05 | P1 | La proyección y reposición contradicen el DRS; hay dos evaluadores incompatibles | IMPLEMENTADO Y VERIFICADO | T01 |
| F06 | P1 | El repositorio no define ni acredita la ejecución diaria del worker | PARCIALMENTE IMPLEMENTADO | T09 |
| F07 | P1 | El PostgreSQL gratuito no cubre continuidad hasta la E3 | PARCIALMENTE IMPLEMENTADO | T13 |
| F08 | P2 | El reemplazo de un producto con historial queda bloqueado por su URL | IMPLEMENTADO Y VERIFICADO | T04 |
| F09 | P2 | Falta indicador de revisión para conteo mayor al teórico | IMPLEMENTADO Y VERIFICADO | T08 |
| F10 | P2 | La respuesta de alerta no incluye la unidad comprometida | IMPLEMENTADO Y VERIFICADO | T08 |
| F11 | P2 | En tránsito convive con cantidad positiva y nueva recomendación de compra | IMPLEMENTADO Y VERIFICADO | T05 |
| F12 | P2 | Bloqueo y pausas son locales al filesystem de cada servicio | IMPLEMENTADO Y VERIFICADO | T09 |
| F13 | P2 | La evidencia de precios queda en almacenamiento efímero y sin retención | IMPLEMENTADO Y VERIFICADO | T15 |
| F14 | P2 | Health check sólo verifica conectividad, no disponibilidad funcional | IMPLEMENTADO Y VERIFICADO | T10 |
| F15 | P2 | La restricción de recepción admite metadatos de media recepción | IMPLEMENTADO Y VERIFICADO | T07 |
| F16 | P2 | El sobre JSON de errores no cubre fallos no controlados | IMPLEMENTADO Y VERIFICADO | T10 |
| F17 | P2 | No existe respaldo diario, retención ni restauración comprobada | PARCIALMENTE IMPLEMENTADO | T17 |
| F18 | P1 | El Plan de Pruebas formal no contiene aceptación ni procedimientos; evidencia E2 es plantilla | PARCIALMENTE IMPLEMENTADO | T14 |
| F19 | P2 | Métricas operacionales y comparación E3 no están implementadas | PARCIALMENTE IMPLEMENTADO | T16 |
| F20 | P3 | Build no reproducible y ausencia de controles CI | IMPLEMENTADO Y VERIFICADO | T18 |
| F21 | P3 | Lecturas reconstruyen toda la historia y toman bloqueos exclusivos | IMPLEMENTADO Y VERIFICADO | T19 |
| F22 | P2 | El worker PostgreSQL termina exitosamente aunque todas las corridas fallen | IMPLEMENTADO Y VERIFICADO | T09 |

El [registro JSON](HALLAZGOS_FINAL.json) conserva causa, evidencia original, impacto, solución, aceptación y esfuerzo, y agrega resultado/pruebas finales. F18: el plan técnico está completo y existe evidencia de arquitectura/backend; incorporar esto a la presentación oficial y crear/enlazar el video exigido por la rúbrica sigue pendiente del equipo. No se afirma que un PR sustituya un video o una aplicación accesible.

## Resultado de las 19 tareas

| ID | Prioridad | Objetivo | Estado final | Resultado y límite | Pruebas |
|---|---|---|---|---|---|
| T01 | P1 | Unificar la proyección y reposición con el DRS | IMPLEMENTADO Y VERIFICADO | Regla RN-07 unificada; Q=17 sin hardcode. Se corrige una expectativa original contraria al DRS. | test_replenishment.py; test_e2_commercial_alert.py; test_rules_health_metrics.py |
| T02 | P0 | Autenticar, autorizar y atribuir todas las operaciones | IMPLEMENTADO Y VERIFICADO | 16 rutas mutantes protegidas (login es público); 401/403, identidad/rol actual, bcrypt, JWT y autoría. | test_auth_security.py; test_e2_completion.py |
| T03 | P1 | Aislar parsing defectuoso y finalizar todas las corridas | IMPLEMENTADO Y VERIFICADO | Parser y fallos aislados; runs cerradas, timestamp/TTL conservados y fallo de BD observable. | test_e2_completion.py; test_rules_health_metrics.py |
| T04 | P2 | Permitir sustitución de producto conservando historia | IMPLEMENTADO Y VERIFICADO | Una URL activa por proveedor; versión histórica preservada; recepción antigua conserva formato. | test_e2_completion.py; test_status_queries.py |
| T05 | P1 | Crear sugerencias persistidas y consultables | IMPLEMENTADO Y VERIFICADO | Snapshot persistido, PENDING única y veto por tránsito/historia/precios insuficientes. | test_e2_completion.py |
| T06 | P1 | Registrar decisión ADMIN y crear una orden una sola vez | IMPLEMENTADO Y VERIFICADO | Actor/fecha/motivo y decisión/OC en una transacción; carrera, retry y rollback. | test_e2_completion.py |
| T07 | P1 | Cerrar aprobación→recepción e integridad de estado de orden | IMPLEMENTADO Y VERIFICADO | Cadena HTTP completa y una ENTRY; checks de media recepción; peso variable e idempotencia. | test_e2_completion.py; test_procurement.py |
| T08 | P2 | Completar campos de conteos y alertas comprometidos | IMPLEMENTADO Y VERIFICADO | needs_review en POST/GET y base_unit en ambas alertas; kg/L/unidad. | test_auth_security.py; test_rules_health_metrics.py |
| T09 | P1 | Programar precios con coordinación entre servicios | PARCIALMENTE IMPLEMENTADO | Implementado y probado localmente; activación/horario real del cron dependen de despliegue autorizado. | test_operations.py |
| T10 | P2 | Dar señales de salud y errores fiables | IMPLEMENTADO Y VERIFICADO | Readiness/esquema y conexión limitada; JSON sanitizado/Request-ID; caída/reconexión real en laboratorio. | test_rules_health_metrics.py; test_config.py; ensayo HTTP real |
| T11 | P1 | Preparar configuración y datos de demostración trazables | IMPLEMENTADO Y VERIFICADO | Otra base vacía *_test reproduce historia/catálogo; no importa precios como vigentes ni crea cuentas. | test_demo.py; tests E2/replay; baseline-proof |
| T12 | P1 | Acreditar ejecución real de Render con un SHA identificable | NO DETERMINABLE | NO VERIFICADO: falta servicio/URL/SHA/logs de Render. No se desplegó ni cambió producción. | Sin prueba remota; verificación local de paquete/HTTP/Blueprint |
| T13 | P1 | Garantizar continuidad de la base durante el proyecto | PARCIALMENTE IMPLEMENTADO | Se propone BD no gratuita y región común. Presupuesto, plan/fecha real y continuidad remota requieren decisión externa. | Configuración inspeccionada; restore aislado |
| T14 | P1 | Completar evidencia técnica formal de Entrega 2 | PARCIALMENTE IMPLEMENTADO | Copia técnica completa. Incorporar arquitectura/evidencia y grabar/enlazar video en presentación formal corresponde al equipo; original no modificado. | 17 criterios/procedimientos y cinco renders; original preservado |
| T15 | P2 | Retener evidencia y observar la operación de precios | IMPLEMENTADO Y VERIFICADO | Snapshot mínimo durable canónico/hash, quote/run y nombres únicos; HTML temporal se elimina. | test_e2_completion.py; test_operations.py |
| T16 | P2 / E3 | Implementar métricas del alcance final sin calibrar el Dataset v2 | PARCIALMENTE IMPLEMENTADO | Indicadores backend observados verificados. Protocolo/línea base y aceptación de definiciones para comparar E3 pendientes; sin simulación E3. | test_rules_health_metrics.py |
| T17 | P2 / E3 | Implementar backups y demostrar restauración | PARCIALMENTE IMPLEMENTADO | Dump real, nueve fechas simuladas→siete copias, restore/usuarios y fallo seguro probados. S3 remoto/cron y siete días reales pendientes. | test_operations.py; test_backups_storage.py |
| T18 | P3 | Hacer reproducible el build y automatizar comprobaciones | IMPLEMENTADO Y VERIFICADO | Resolución con hashes y Python fijado; workflow Linux con PG17/18 y build Docker. CI se registra después del push. | dos instalaciones; pip check; pip-audit; CI_RESULTADO.json |
| T19 | P3 | Medir y reducir trabajo repetido sólo donde afecte el MVP | IMPLEMENTADO Y VERIFICADO | 19→16 consultas en el caso de tres insumos sin quitar locks de snapshot; benchmark descriptivo, sin prometer SLA no acordado. | 16 consultas/3 insumos; 28 HTTP/14 clientes concurrentes |

[BACKLOG_FINAL.json](BACKLOG_FINAL.json) conserva además los componentes, dependencias, cambios y criterios de aceptación originales. Conteo: {'IMPLEMENTADO Y VERIFICADO': 13, 'PARCIALMENTE IMPLEMENTADO': 5, 'NO DETERMINABLE': 1}. T12 depende de acceso/autoridad remotos; T13 de presupuesto/servicio; T09/T17 de despliegue/almacenamiento; T14 de documentación audiovisual del equipo; T16 del protocolo E3. La implementación local de estos frentes se entrega sin ejecutar las acciones externas prohibidas.

## Verificación ejecutada

| Control | Resultado real |
|---|---|
| Suite PostgreSQL 17 / 18 | 357/357 cada una, 0 fallos/errores/skips; una advertencia Starlette/httpx visible. |
| Originales y Dataset v2 | 220 casos conservados, un nombre/expectativa corregidos por DRS; dataset sin cambios. |
| Ruff / formato / git diff --check | Pasan sobre el backend actual. |
| Dependencias runtime | pip-audit actual sin vulnerabilidades conocidas identificadas; no prueba ausencia universal. |
| Build/instalación | Wheel producido, CA incluida y módulos nuevos presentes; dos resoluciones iguales, sin pytest instalado, pip check OK. |
| Migraciones | Desde vacío y 0004 con datos en ambas versiones; prueba de rechazo/rollback ante media recepción; alembic check. |
| Paquete instalado / HTTP real | Login, lectura, CORS, tres corridas de proveedores, sugerencia, decisión y recepción con retry. |
| Caída/reconexión local | /ready→503 en 3019.519 ms; /live sigue vivo y BD recupera servicio sin reiniciar API. |
| Backups/restore | Dump y restore PostgreSQL reales, identidad de ambos roles, retención y fallos; S3 con mocks. |
| Docker/Linux local | No ejecutado: daemon Docker no disponible; resultado Linux/contenedor se acredita por CI si PASS. |
| Render remoto | NO VERIFICADO. Sin URL/SHA/config/logs atribuibles y sin cambios remotos. |
| Plan formal | Copia técnica con 17 criterios, procedimientos y complejidades; contenido original A:C preservado; cinco renders inspeccionados. |

Comandos reproducibles (desde `backend`, únicamente en base descartable `_test`):

```text
python -m pip install --require-hashes -r requirements-test.lock
python -m pip install --no-deps -e .
python -m pip check
python -m ruff check .
python -m ruff format --check .
python -m pytest -q --junitxml=<ruta_privada>/results.xml
python -m pip_audit -r requirements.lock --no-deps --disable-pip
python -m pip wheel --no-deps --wheel-dir <ruta_privada> .
```

La fixture elimina/recrea public de la base de tests declarada. No ejecutar esa suite en una base a conservar. Los XML/logs íntegros de laboratorio están fuera de Git en el directorio privado temporal de esta ejecución; VERIFICACION.json conserva sus hashes, conteos y grupos. Una prueba no realizada se mantiene pendiente.

## Render y publicación

Build: `pip install --require-hashes -r requirements.lock && pip install --no-deps .`. Start: `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Revisión esperada: 0005. Health: `/ready`; liveness `/live`; identidad `/version`. No fallback silencioso a localhost. PostgreSQL 17/18 comprobados localmente; TLS/red/conexión del proveedor final pendientes.

Variables obligatorias de API: `STOCKSMART_DATABASE_URL`, `STOCKSMART_JWT_SECRET`; configurar `STOCKSMART_CORS_ORIGINS`, entorno y TTL según [OPERACION.md](OPERACION.md). El cron de precios usa la URL de BD y catálogo activo. Backup requiere bucket privado y credenciales AWS dedicadas; nombres y límites completos en el runbook. Las cuentas/catálogo se aprovisionan por procedimientos controlados.

El Blueprint propone BD con continuidad y cron diarios, región común, `branch: main` y `autoDeployTrigger: off`. Los planes de pago son propuestas sin compra/aplicación. Los nombres legacy siguen admitidos según [Render Compute Plans](https://render.com/docs/compute-plans). La validación estructural no comprueba capacidad, cuota, credenciales ni un servicio existente.

Antes de publicar se consultaron ramas remotas, hooks, workflows y deployments visibles: la rama fix no existía; el repositorio no tenía hooks/workflows/deployments publicados. No hay credencial Render detectada ni canal autenticado. Esa ausencia no demuestra que no exista un servicio integrado mediante GitHub App. Según [Render Deploys](https://render.com/docs/deploys), los autodeploys corresponden a la rama vinculada; se publica una rama nueva y el Blueprint liga main. Los commits incluyen además `[skip render]`, marcador oficial que evita autodeploy sin omitir CI. No se modifica main ni se llama a deploy hooks. La configuración de servicios remotos ya existentes no puede darse por auditada.

Antes de operar: identificar propietario/servicio real y SHA, aprobar presupuesto y plan/continuidad, aplicar migraciones en etapa autorizada, configurar secretos/usuarios/catálogo/bucket, verificar HTTPS/CORS/health/lecturas y persistencia en entorno permitido, activar cron sin duplicados, observar copias y restauración. El código no sustituye esos pasos externos. Una mera respuesta health verde no acredita el flujo de negocio.

## Checklist de entrega

- [x] Implementación backend, reglas, roles, persistencia y pruebas locales completas.
- [x] Build de producción, instalación del paquete y arranque HTTP real en laboratorio.
- [x] Plan técnico trazable; originales, Dataset v2 y frontend preservados.
- [x] Diff revisado y escaneo acotado de credenciales sin hallazgos activos identificados.
- [ ] Equipo: incorporar arquitectura/evidencia y video o aplicación al informe de avance oficial; validación humana y créditos de IA.
- [ ] Infraestructura: verificar/provisionar Render, continuidad, secretos, bucket y programación real; sólo en etapa autorizada.
- [ ] E3: aprobar protocolo/línea base y medir impacto; conservar Dataset v2 congelado.

La conclusión es favorable para publicar estos cambios de backend en una rama revisable si los controles finales se mantienen. No equivale a aceptar la Entrega 2 completa, acreditar producción ni demostrar metas comerciales.

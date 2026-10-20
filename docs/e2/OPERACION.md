# Operación del backend StockSmart E2

Estado: configuración y herramientas versionadas. No se ha desplegado ni modificado producción. Ver [informe de validación](INFORME_IMPLEMENTACION.md) antes de operar.

## Entorno e instalación

Python 3.12.15, PostgreSQL 17 o 18. La actualización conserva la rama 3.12 e incorpora correcciones de seguridad publicadas en [Python 3.12.15](https://www.python.org/downloads/release/python-31215/). Render admite especificar una versión publicada mediante `PYTHON_VERSION` ([documentación](https://render.com/docs/python-version)).

Desde `backend`, crear un venv privado. Instalar producción con `python -m pip install --require-hashes -r requirements.lock` y `python -m pip install --no-deps .`. Para desarrollo, usar `requirements-test.lock` y `pip install --no-deps -e .`. Los locks son universales para Python 3.12 y contienen hashes; no incorporan un índice privado. El build usa setuptools 84.0.0 fijado. Para actualizar, resolver ambos locks, auditar y repetir la matriz PostgreSQL.

Regeneración empleada: `uv pip compile pyproject.toml --python-version 3.12 --universal --generate-hashes --no-header --no-emit-index-url -o requirements.lock`; añadir `--extra test` para `requirements-test.lock`. uv es una herramienta de mantenimiento; no es dependencia de runtime ni de Render.

## Variables (sólo nombres)

| Variable | Uso |
|---|---|
| `STOCKSMART_DATABASE_URL` | Obligatoria. PostgreSQL/psycopg; sin respaldo silencioso a localhost. |
| `STOCKSMART_JWT_SECRET` | Obligatoria para API. Secreto aleatorio exclusivo, mínimo 32 bytes; nunca una contraseña humana. |
| `STOCKSMART_JWT_TTL_MINUTES` | 1–60; defecto 30. Rotar firma invalida tokens existentes. |
| `STOCKSMART_CORS_ORIGINS` | Orígenes HTTPS explícitos del consumidor. Sin comodín ni cookies. |
| `STOCKSMART_DB_CONNECT_TIMEOUT` | 1–10 s, defecto 3. Runtime: consultas/locks 10/5 s; migraciones: 60/5 s. |
| `STOCKSMART_ENVIRONMENT` | Identifica el entorno operativo; no habilita bypass de autenticación. |
| `RENDER_GIT_COMMIT`, `STOCKSMART_BUILD_SHA` | Identidad de build para `/version`; sin dato válido devuelve null. |
| `STOCKSMART_PRICE_EVIDENCE_DIR` | Raíz temporal opcional. El snapshot comercial durable está en PostgreSQL. |
| `STOCKSMART_BACKUP_BUCKET` | Bucket privado exclusivo de backups; requiere bloquear acceso público. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` | Sólo servicio de backup, con permisos mínimos y rotación. |
| `STOCKSMART_BACKUP_ENDPOINT` | Opcional, HTTPS, S3 compatible con los controles requeridos. |
| `STOCKSMART_PG_BIN` | Directorio de pg_dump/pg_restore. Docker incluye cliente PostgreSQL 18. |
| `STOCKSMART_TEST_DATABASE_URL` | Exclusiva para tests; base PostgreSQL nueva y descartable con sufijo `_test`. |

No imprimir variables ni URLs con credenciales en evidencia. `.env.example` describe nombres; la aplicación no carga `.env` automáticamente. Para enlaces externos PostgreSQL, utilizar TLS según proveedor (`sslmode=require` o validación de certificado con `verify-full` cuando se configure la CA). El enlace interno de Render requiere comprobar red y negociación TLS en el entorno real. No usar URL/credenciales del laboratorio en operación.

## Migraciones, arranque y usuarios

1. Respaldar y ensayar upgrade en una copia aislada del estado anterior.
2. Desde `backend`: `alembic upgrade head`; revisión esperada `0005_e2_completion`.
3. Si hay PENDING duplicadas o metadatos de recepción incompletos, `0005` falla sin borrar/reconciliar historia automáticamente. Identificar filas y resolverlas mediante una decisión operativa trazable antes de repetir. No modificar una migración aplicada ni forzar `stamp` para ocultar el fallo.
4. Aprovisionar cada cuenta mediante consola privada: `python -m app.manage_users <email> --name <nombre> --role ADMIN` o `WAREHOUSE`. La contraseña se ingresa dos veces con getpass, 12–72 bytes UTF-8, bcrypt coste 12. No hay cuentas predeterminadas, registro público ni tokens permanentes. Repetir sobre la misma identidad rota contraseña/datos; `--disable` desactiva y revoca acceso en cada petición. Los tokens ya emitidos mantienen su TTL al cambiar sólo contraseña; desactivar o rotar firma si se necesita revocación inmediata.
5. Ejecutar `uvicorn app.main:app --host 0.0.0.0 --port $PORT` en Render. El comando versionado migra antes de iniciar.
6. Comprobar `/ready` y `/health` (conexión y esquema), `/live` (proceso) y `/version` (build, sin inventar SHA). Después login y un GET autenticado de inventario. Un health verde no prueba todo el flujo.

La propuesta usa un web service starter para disponer de consola controlada. La ejecución de aprovisionamiento y cualquier cambio de plan se realizan sólo en una etapa de despliegue autorizada. No colocar credenciales iniciales en el código, el Blueprint o argumentos de shell.

## Catálogo, precios y sugerencias

Configurar insumos y proveedores/productos mediante las rutas ADMIN del [contrato](../INTEGRACION_BACKEND.md). Las URLs y metadata del fixture son evidencia fechada; verificar vigencia/identidad antes de usarlas operacionalmente. Los plazos del laboratorio son supuestos sintéticos. Conservar la versión antigua y crear una nueva activa si cambia identidad/formato bajo la misma URL.

El worker `python -m scraper.postgres --all-active` procesa los insumos con asociaciones activas. `--item-id N` selecciona uno. Requiere migraciones aplicadas. API y cron adquieren un bloqueo PostgreSQL antes del HTTP; no dependen de compartir filesystem. El reloj de PostgreSQL coordina la pausa de dos segundos por host. Un proceso muerto pierde su lock de sesión; la siguiente ejecución cierra corridas abandonadas. No se mantienen locks de inventario durante HTTP.

| Exit | Significado |
|---|---|
| 0 | Todas las corridas terminaron OK. |
| 1 | Fracaso total, infraestructura o ningún insumo/proveedor activo. |
| 2 | Resultado parcial, requiere revisar corridas. |
| 3 | Otro worker posee la exclusión. No ejecutar en paralelo ni saltar el bloqueo. |

Monitorear exit no cero, `scrape_runs` ERROR/PARTIAL y ausencia de actualización diaria. El error de un proveedor conserva el último precio vigente y permite continuar con otros; la fecha no se renueva. Disponibilidad negativa posterior impide reactivar una quote antigua mediante fallback.

La evidencia permanente es un snapshot comercial mínimo y SHA-256 canónico enlazados a la quote, con hash del HTTP original. Se conserva junto al histórico de precios y en sus backups. No permite reconstruir el HTML completo descartado; sí recalcular y verificar los datos comerciales conservados. El HTML temporal único se elimina al cerrar. No publicar capturas crudas ni secretos embebidos por proveedores.

Generar sugerencia ADMIN, consultar snapshot y decidir. Con tránsito, no generar otra. Una sugerencia vencida o incompatible requiere rechazo documentado y nueva generación. Aprobar sólo crea una OC interna. La recepción registra el peso real medido cuando el formato es variable; el valor nominal de la orden no cambia. Reintentos idénticos no duplican entradas. No se ejecutan compras externas.

## Demostración reproducible y nueva operación

Demostración: crear una base **vacía, aislada y descartable** `_test`, aplicar migraciones y ejecutar `python -m app.demo --fixture-root <repo>/backend/tests/fixtures/e2`. El comando conserva Dataset v2, semillas, merma causal y reglas D1–D7; no importa precios como actuales ni crea usuarios por defecto. Rechaza una base operacional y una segunda carga con inventario existente. Aprovisionar cuentas controladas y adquirir precios mediante el worker/API por separado. El replay de recepciones del dataset usa el puerto de prueba E2; no se presenta como decisiones humanas HTTP. El flujo operacional HTTP se valida por las nuevas pruebas y por el ensayo HTTP aislado.

Nueva operación sin historia: crear stock inicial en el momento real del registro. No retroceder `observation_started_at` ni fabricar historia para evitar D7. Sin un día completo, consumo/cantidad son null; con menos de siete días, estimación preliminar. Un umbral manual permite alertar, pero no inventa consumo ni genera una cantidad calculable. No confundir fixture con operación real o con comparación E3.

## Backups y restauración

`python -m app.backups` realiza pg_dump custom a temporales privados, genera manifiesto/hash y sube dump + manifiesto a bucket privado con cifrado AES256 del servidor. Sólo después de publicar el manifiesto aplica retención de siete fechas completas. Lista todas las páginas. Falla si no certifica bloqueo público, si un endpoint usa HTTP, si falla pg_dump/upload/retención o si falta configuración. No coloca URL/contraseña en argv ni en el manifiesto. Guardar el bucket fuera de Git y restringir acceso por IAM. Permisos: GetBucketPublicAccessBlock, ListBucket y Get/Put/DeleteObject sólo para el prefijo `stocksmart-backup-*`; sin permisos públicos.

El Dockerfile instala cliente 18, compatible con servidores 17/18, y ejecuta sin root. El cron de backup está definido cada 24 h, pero bucket/credenciales/calendario remoto no se han provisionado ni verificado. Una simulación de nueve fechas verifica siete copias; no acredita siete días transcurridos de operación. Comprobar además versionado/retención del proveedor si se desea protección frente a borrado por un operador.

Laboratorio de restore: descargar una copia a directorio privado y verificar el hash. Configurar una base nueva/vacía `_test` y ejecutar `python -m app.backups --restore <directorio>`. Rechaza una base no `_test`, un hash distinto y un destino con tablas. Usa una transacción y detiene ante error; no usa `--clean`. Verificar revisión, usuarios de aplicación/bcrypt/roles, conteos, ledger, órdenes/vínculos y acceso de ambos roles. Los roles administrados de PostgreSQL los provisiona el proveedor; no se exportan contraseñas del servidor. Para volúmenes privados del operador existe `--local-dir`; no usar filesystem efímero de Render como única copia.

## Render: etapa posterior autorizada

En la rama preparada el 19/10/2026, el Blueprint define `codex/deploy-render` para API y ambos cron, autodeploy `off`, Python fijado, región común y PostgreSQL 17 con plan no gratuito. Ver [guía de despliegue](../DEPLOY_RENDER.md) y [auditoría de ramas](../AUDITORIA_RAMAS_2026-10-19.md). El informe y la verificación del 18/10 conservan su carácter de evidencia histórica del cierre `66422a0`. El almacenamiento privado y las credenciales se configuran fuera de Git. Los planes con costo son una propuesta, no una compra ni un cambio aplicado. [Referencia Blueprint](https://render.com/docs/blueprint-spec), [cron](https://render.com/docs/cronjobs), [despliegues y rama vinculada](https://render.com/docs/deploys).

Antes de aplicar: confirmar propietario/servicios existentes para evitar duplicación; presupuesto; fecha de creación/expiración y plan real de BD; backup/restore; origen real; secretos; commit; permisos; bucket privado; cliente pg_dump; orden de activación. Migrar y verificar API antes de activar jobs. No mantener una base free con vencimiento anterior a E3 sin transición probada. No se conocen aquí la URL, plan, fecha o SHA remotos actuales.

Tras autorización de despliegue: guardar SHA de `/version` y logs de build/start, revisión Alembic, HTTPS, CORS, login/GET autenticado, persistencia tras reinicio en un entorno autorizado de prueba, corrida diaria y evidencia de siete backups/restore. Probar escrituras sólo sobre datos de demostración autorizados. La publicación de `codex/deploy-render` prepara la rama y no hace merge a `main`. La configuración futura no cambia automáticamente un servicio ya conectado a otra rama.

## Alcance de métricas

El endpoint entrega indicadores observacionales y los ids que los sustentan. Usa períodos con zona y límites `[start,end)`, sin sumar kg/L/unidad. Un dato ausente se conserva como null. Las definiciones explícitas están en el contrato y en la respuesta. El equipo debe validar la aceptación de estas definiciones para E3 y aportar el protocolo/línea base comparable. No ejecutar la simulación comparativa ni calibrar Dataset v2 a 30/20/5 como parte de esta corrección.

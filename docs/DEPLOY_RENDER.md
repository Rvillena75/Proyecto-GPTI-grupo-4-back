# Desplegar StockSmart en Render

Rama preparada: [`codex/deploy-render`](https://github.com/Rvillena75/Proyecto-GPTI-grupo-4-back/tree/codex/deploy-render). Ver [auditoría y diferencias entre ramas](AUDITORIA_RAMAS_2026-10-19.md). Este documento prepara la operación: todavía no acredita un servicio StockSmart desplegado.

## 1. Elegir el alcance del primer despliegue

Para levantar primero la API, crear PostgreSQL y un **Web Service con runtime Python 3** mediante la configuración de abajo. Es posible dejar precios/backups para después de disponer de catálogo y bucket. No aplicar además el Blueprint como una segunda instalación: eso puede duplicar recursos.

Para instalar el conjunto completo, usar el `render.yaml` de esta rama como Blueprint. Declara PostgreSQL, API y dos cron; requiere presupuesto y secretos del almacenamiento S3 antes de aplicarlo. Las tres declaraciones de servicio apuntan a esta misma rama. No se han creado ni comprado esos recursos durante la preparación.

La propuesta versionada usa PostgreSQL `basic-256mb` y Web Service `starter`, ambos de pago. El Web Service de pago permite Shell/SSH para aprovisionar usuarios con la herramienta privada existente. El operador debe revisar la tarifa vigente y aprobar el coste en Render antes de crear los servicios.

## 2. PostgreSQL y Web Service

Crear una base nueva PostgreSQL **17**, en **Oregon**, y vincular su **Internal Database URL** al Web Service en la misma región. PostgreSQL 18 también está cubierto por la CI; el Blueprint fija 17. Conservar el acceso externo restringido; el Blueprint lo desactiva con `ipAllowList: []`.

| Campo del Web Service | Valor |
|---|---|
| Repository | `Rvillena75/Proyecto-GPTI-grupo-4-back` |
| Branch | `codex/deploy-render` |
| Runtime / Language | **Python 3** |
| Root Directory | `backend` |
| Build Command | `pip install --require-hashes -r requirements.lock && pip install --no-deps .` |
| Start Command | `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health Check Path | `/ready` |
| Region | Oregon, igual que PostgreSQL |
| Auto-Deploy | Off |

**No seleccionar Docker para la API:** el Dockerfile existente es la imagen del proceso de backups. No levantaría el servidor web con su comando predeterminado.

Configurar estas variables en el servicio antes del primer arranque:

| Variable | Valor o procedencia |
|---|---|
| `PYTHON_VERSION` | `3.12.15` |
| `STOCKSMART_ENVIRONMENT` | `production` |
| `STOCKSMART_DATABASE_URL` | Internal Database URL de la base recién elegida. El backend adapta `postgres://`/`postgresql://` a psycopg. |
| `STOCKSMART_JWT_SECRET` | Secreto aleatorio exclusivo, mínimo 32 bytes; el Blueprint lo genera. En creación manual usar el generador de secretos del operador. |
| `STOCKSMART_JWT_TTL_MINUTES` | `30` |
| `STOCKSMART_DB_CONNECT_TIMEOUT` | `3`, opcional porque es el valor predeterminado |
| `STOCKSMART_CORS_ORIGINS` | Origen HTTPS real del frontend; varios orígenes separados por coma. El Blueprint conserva `https://stocksmart-nine.vercel.app`, cuya vigencia debe confirmar el equipo. |
| `STOCKSMART_DAILY_SCRAPE_ENABLED` | `true` (predeterminado en Render cuando `RENDER=true`) para habilitar el programador diario desatendido dentro del Web Service. |
| `STOCKSMART_DAILY_SCRAPE_TIMEZONE` | `America/Santiago` (predeterminado), maneja automáticamente horario de invierno (UTC-4) y horario de verano (UTC-3) en Chile. |
| `STOCKSMART_DAILY_SCRAPE_HOUR` | `7` (07:00 hora local de Chile). |

No copiar variables del laboratorio ni definir `STOCKSMART_TEST_DATABASE_URL` en producción. La aplicación no carga `.env` automáticamente. No enviar URLs con credenciales, contraseñas o secretos por chat ni incorporarlos al repositorio. Render aporta `RENDER_GIT_COMMIT` para identificar el código en `/version`.

## 3. Primer arranque y comprobación

1. Confirmar que la [CI de `codex/deploy-render`](https://github.com/Rvillena75/Proyecto-GPTI-grupo-4-back/actions?query=branch%3Acodex%2Fdeploy-render) está verde para el SHA que se desplegará. En Render, seleccionar ese commit mediante el despliegue manual. `[skip render]` omite el automático, no impide seleccionar el commit manualmente.
2. Revisar logs de build y arranque. La instalación debe completar los locks y Alembic llegar a `0005_e2_completion` antes de iniciar Uvicorn. Si se reutiliza una base con datos, hacer antes backup y ensayo en una copia aislada; no forzar `stamp` ni borrar historia para resolver una migración fallida.
3. Abrir la URL HTTPS que Render asigne y añadir `/ready`: debe responder `200` con `{"status":"ok"}`. `/live` debe responder `{"status":"alive"}`. `/version` debe mostrar el SHA desplegado y `expected_schema_revision: "0005_e2_completion"`. Un estado `Running` por sí solo no comprueba estos contratos.
4. Abrir `/docs` para el contrato OpenAPI. Un `GET /items` sin Bearer debe devolver **401**: indica que la protección de la API está activa. La raíz `/` no es una página de frontend.
5. En la **Shell privada del Web Service**, ejecutar `python -m app.manage_users <email> --name <nombre> --role ADMIN`, sustituyendo email y nombre por los del operador autorizado. La CLI pide dos veces una contraseña de 12–72 bytes UTF-8 con `getpass`; no hay cuentas iniciales ni contraseña predeterminada. Usar `WAREHOUSE` para las cuentas de bodega.
6. En `/docs`, usar `POST /auth/login` con la cuenta creada, copiar el `access_token` al control **Authorize** de Bearer y comprobar `GET /auth/me` y `GET /items`. Conservar SHA, URL y logs sin credenciales como evidencia. Si se prueban escrituras, usar exclusivamente registros de demostración autorizados para ese entorno.

No ejecutar `app.demo`, replay de E2 ni importar el Dataset v2 en una base operacional. Sus datos son fixtures sintéticos y sus guardas exigen bases de prueba. Los tests de arranque crean su propia base descartable; ese procedimiento no sirve para configurar producción.

## 4. Precios y backups

Activar los jobs después de verificar la API y completar sus insumos externos. Si se creó la API manualmente, configurar estos servicios en la misma instalación; no volver a provisionar toda la base/API por Blueprint.

| Servicio | Configuración |
|---|---|
| `stocksmart-daily-prices` | Rama `codex/deploy-render`, runtime Python, Root Directory `backend`, mismo Build Command y Python que la API, `python -m scraper.postgres --all-active --only-if-due`, cron `0 10 * * *` UTC (07:00 Chile verano / 06:00 Chile invierno), `STOCKSMART_DAILY_SCRAPE_TIMEZONE=America/Santiago`, misma base interna. Coordinado mediante advisory lock en PostgreSQL con el programador diario integrado de la API para evitar corridas duplicadas o simultáneas. |
| `stocksmart-daily-backup` | Rama `codex/deploy-render`, runtime Docker, sin Root Directory, Dockerfile `./backend/Dockerfile`, Docker Context `./backend`, comando `python -m app.backups`, cron `0 8 * * *` UTC, misma base interna. |

El backup requiere `STOCKSMART_BACKUP_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` y `AWS_DEFAULT_REGION` (Blueprint: `us-west-2`). Son entradas privadas del servicio de backup. Para un destino S3 compatible existe `STOCKSMART_BACKUP_ENDPOINT` HTTPS opcional. El bucket debe bloquear acceso público y los permisos deben limitarse al uso requerido por la herramienta. El contenedor incorpora cliente PostgreSQL 18 para respaldar servidores 17/18.

La creación del cron no acredita una corrida correcta. Registrar la ejecución real de precios y el almacenamiento/retención de siete copias, y ensayar restauración solo en una base nueva vacía `*_test` según [operación](e2/OPERACION.md). La disponibilidad y permisos del bucket no se pueden comprobar desde el código versionado.

## 5. Qué acredita la rama

La CI configura dos jobs Linux con PostgreSQL 17/18: pruebas, migraciones, Ruff, auditoría de dependencias, wheel, ensayo HTTP de ese paquete instalado en un entorno sin pytest y, en PostgreSQL 18, build Docker con el contexto de backups. El ensayo comprueba salud, identidad del build, rechazo sin token, login, rol y persistencia autorizada. Solo un resultado verde de ambos jobs acredita ese SHA.

El esquema oficial del Blueprint y las rutas de build se validan durante la preparación. Aun con la CI verde, URL, red, secretos, CORS, logs, datos, cron, almacenamiento y presupuesto requieren la comprobación del entorno real. El despliegue efectivo es un paso posterior.

Documentación oficial: [Blueprint](https://render.com/docs/blueprint-spec), [primer despliegue](https://render.com/docs/your-first-deploy), [Shell/SSH](https://render.com/docs/ssh), [variables automáticas](https://render.com/docs/environment-variables) y [control de despliegues](https://render.com/docs/deploys).

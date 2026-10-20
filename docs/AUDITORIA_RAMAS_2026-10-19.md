# Auditoría de ramas y preparación para Render

Fecha: 19/10/2026. Repositorio: [Proyecto-GPTI-grupo-4-back](https://github.com/Rvillena75/Proyecto-GPTI-grupo-4-back). Alcance: ramas remotas, diferencias de código/configuración, historial, CI y preparación de una rama de despliegue. No se aplicaron recursos ni migraciones en Render.

## Inventario remoto de partida

Se consultaron las referencias de GitHub y se actualizaron las referencias locales con `git fetch origin` antes de comparar.

| Rama | SHA auditado | Evaluación |
|---|---|---|
| `integration/stocksmart-20261012` | `3df6859aaa09e21444a9b371dbbe25c590bb2b41` | Integrada completamente en `main`; le faltan los dos commits posteriores de `main`, incluida la corrección TLS. |
| `main` | `5333503b530251e945427b864f08b0ab7db1d300` | Contiene la integración y TLS, pero no el cierre E2 de autenticación, roles, sugerencias y operación. No es la base elegida para publicar la API. |
| `fix/backend-e2-audit-completion` | `66422a092a7b121cbf2dd730c97dfc0a8013bdab` | Contiene todo `main` y dos commits de cierre E2. Mejor base disponible, con CI verde en PostgreSQL 17 y 18. |

Las comparaciones `git rev-list --left-right --count` devolvieron `0 2` tanto para `integration/stocksmart-20261012...main` como para `main...fix/backend-e2-audit-completion`. No hay cambios exclusivos de las dos ramas anteriores que sea necesario fusionar a la rama de corrección.

La PR [#1](https://github.com/Rvillena75/Proyecto-GPTI-grupo-4-back/pull/1) ya se fusionó. La PR [#2](https://github.com/Rvillena75/Proyecto-GPTI-grupo-4-back/pull/2) permanece abierta contra `main`; preparar la rama de despliegue no la fusiona. Los checkpoints locales `backup/main-before-integration-20261014` y `recovery/stocksmart-20261012-original` no figuraban como ramas remotas. Se conservaron sin fusionar ni borrar.

## Hallazgos y tratamiento

| ID / prioridad | Evidencia e impacto | Tratamiento en la rama de despliegue |
|---|---|---|
| BR01 / P0 | `main` no contiene `backend/app/api/auth.py`; sus rutas de inventario no requieren los usuarios/roles del cierre E2. Publicarlo expondría operaciones sin esos controles. | Partir de `66422a0`, que incorpora JWT, ADMIN/WAREHOUSE y sus pruebas. |
| BR02 / P1 | Los tres servicios del `render.yaml` de la rama de corrección declaraban `branch: main`. Aplicar ese Blueprint podía construir el backend antiguo aunque se seleccionara la rama de corrección para el Blueprint. | API, precios y backups declaran explícitamente `codex/deploy-render`. |
| BR03 / P2 | La CI aceptaba pushes a `main` y `fix/**`, pero no a una rama de despliegue con otro nombre. | Añadir el nombre exacto `codex/deploy-render` al disparador de CI. |
| BR04 / P1 | Backup combinaba `rootDir: backend` con `dockerfilePath: ./Dockerfile`, sin contexto explícito. La especificación de Render describe esas rutas Docker respecto de la raíz del repositorio. Riesgo de seleccionar un archivo/contexto incorrectos; no se reprodujo un fallo remoto. | Eliminar `rootDir` de ese servicio, usar `./backend/Dockerfile` y `dockerContext: ./backend`. La CI construye con esas rutas explícitas. |
| BR05 / P1 | `backend/Dockerfile` ejecuta `python -m app.backups`; no sirve HTTP. Seleccionarlo por detección automática para la API produciría el proceso equivocado. | Guía con runtime **Python 3** para el Web Service; Docker reservado al cron de backups. |
| BR06 / P2 | La suite existente validaba el backend y la construcción del wheel, pero no el arranque HTTP de ese wheel aislado de pytest y del árbol fuente en Linux. | Añadir ensayo de producción instalado a ambos jobs PostgreSQL de GitHub Actions. |
| BR07 / dependencia externa | La captura aportada muestra un servicio Ruby `2025-1-grupo-59` en el workspace visible; no acredita un despliegue de StockSmart. No se conocen una URL, SHA o logs remotos de StockSmart. | Mantener pendiente la verificación remota y documentar los pasos exactos para provisionar y comprobarla. |

P0 identifica el riesgo de elegir `main` para el despliegue; no afirma que se haya expuesto esa rama en un servicio real.

## Rama resultante y cambios

Rama destinada a Render: [`codex/deploy-render`](https://github.com/Rvillena75/Proyecto-GPTI-grupo-4-back/tree/codex/deploy-render), derivada de `66422a0`. Los cambios de preparación son configuración, CI, el script `scripts/check_render_startup.py` y documentación. No modifican `backend/app`, migraciones, dependencias bloqueadas, pruebas existentes ni Dataset v2.

No se alteran `main`, la rama de integración ni la rama de corrección. No se usa force push. El despliegue automático del Blueprint permanece desactivado; un servicio ya existente conserva su configuración hasta que un operador la cambie. El commit de preparación usa `[skip render]` para evitar un despliegue automático al publicar la rama.

## Verificación y límites

Evidencia previa del SHA base `66422a0`: [CI push](https://github.com/Rvillena75/Proyecto-GPTI-grupo-4-back/actions/runs/39175105118) y [CI de PR](https://github.com/Rvillena75/Proyecto-GPTI-grupo-4-back/actions/runs/39175108392), ambos correctos con **357 pruebas por versión de PostgreSQL** (17 y 18). El informe E2 del 18/10 sigue describiendo ese cierre histórico; esta preparación no reemplaza sus resultados ni fingerprints.

Antes de publicar la rama se ejecutó el nuevo ensayo sobre PostgreSQL 17 local aislado y Python 3.12.15 con el wheel del backend base y solo dependencias de producción: **PASS**. Crea una base descartable propia, migra hasta `0005_e2_completion`, arranca Uvicorn desde un directorio temporal y verifica `/ready`, `/live`, SHA en `/version`, 401 sin token, login, rol y escritura/lectura autorizada de inventario. Elimina su propia base al terminar. No consulta proveedores ni una base remota.

La aceptación del nuevo SHA requiere que ambos jobs de la [CI de la rama](https://github.com/Rvillena75/Proyecto-GPTI-grupo-4-back/actions?query=branch%3Acodex%2Fdeploy-render) terminen correctamente, incluido el ensayo con el paquete recién construido. También se validan Ruff, dependencias y la construcción del contenedor de backup. La identidad definitiva del commit y el resultado de esos jobs se comprueban después de publicar; esta descripción no sustituye ese resultado.

**Preparación técnica no equivale a despliegue remoto verificado.** Siguen pendientes la creación/selección de recursos, presupuesto, secretos, origen CORS efectivo, usuarios, catálogo, bucket privado, ejecución real de cron/backup y comprobación de URL/SHA/logs. No se importó el Dataset v2 en operación ni se ejecutó la simulación de impacto E3. Procedimiento: [DEPLOY_RENDER.md](DEPLOY_RENDER.md).

## Fuentes de configuración

Documentación oficial consultada el 19/10/2026: [Blueprint: branch, runtime, Docker y autoDeployTrigger](https://render.com/docs/blueprint-spec), [monorepos](https://render.com/docs/monorepo-support), [variables automáticas](https://render.com/docs/environment-variables), [Shell/SSH](https://render.com/docs/ssh) y [marcadores para omitir despliegues](https://render.com/docs/deploys). La evaluación de ramas procede del historial y archivos del repositorio, no de una afirmación de despliegue de esas fuentes.

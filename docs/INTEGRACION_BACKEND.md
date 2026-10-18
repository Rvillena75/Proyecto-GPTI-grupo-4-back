# Contrato de integración — 18/10/2026

Fuentes: DRS E2 §7–§10, `Arquitectura.md` v1.2, precisiones de `docs/decisions.md`, contratos `RawQuote`, `PackSpec` y recepción E2. El DRS prevalece ante contradicciones. El contrato generado está en `/openapi.json`; evidencia en [informe](e2/INFORME_IMPLEMENTACION.md). Backend únicamente.

## Responsabilidades y estado

- Inventario, catálogo, precios, generación persistida, decisiones y recepción están conectados mediante HTTP y PostgreSQL.
- Todas las rutas de negocio exigen Bearer. `/auth/login`, `/health`, `/ready`, `/live`, `/openapi.json` y documentación son públicas.
- ADMIN configura insumos/umbrales/proveedores/productos, refresca precios, genera/decide sugerencias y consulta métricas. ADMIN y WAREHOUSE registran movimientos/conteos/recepciones y consultan información operativa. DRS §5 y RNF-09 fijan configuración/decisión; los permisos de refresco/generación se asignan a ADMIN por ser acciones de compra. No hay registro público ni asignación de roles desde un request.
- Las pruebas originales de recepción siguen usando fixtures aislados; las nuevas pruebas recorren sugerencia → decisión → orden → recepción íntegramente por HTTP, sin insertar aprobaciones.
- Servicio/URL/SHA remotos de Render sin verificar. La propuesta de infraestructura no aplica cambios remotos.

## Autenticación

`POST /auth/login` recibe `{"email":"...","password":"..."}` y devuelve `access_token`, `token_type="bearer"`, `expires_in`. Enviar `Authorization: Bearer <access_token>` en las rutas protegidas. `GET /auth/me` devuelve `id/name/email/role`. Token HS256 con emisor/audiencia fijos, claims obligatorios y duración configurable de 1–60 minutos (30 por defecto). Se verifica usuario activo y rol vigente en cada solicitud; no se confía en un rol del JWT o del cuerpo. Login limita a seis fallos por identidad en una ventana de 15 minutos, compartida por PostgreSQL. No se guardan contraseñas en logs; bcrypt usa coste 12 y máximo 72 bytes. Aprovisionamiento: [operación](e2/OPERACION.md).

Ausente/inválido/vencido/inactivo: 401. Rol incorrecto: 403. Login temporalmente bloqueado: 429. No usar cookies ni parámetros de URL para transportar tokens.

## Proveedores y productos

| Ruta | Comportamiento |
|---|---|
| `GET/POST /suppliers` | Listar o crear proveedor; nombre único, plazo no negativo. |
| `GET/PATCH /suppliers/{id}` | Consultar/editar solo los campos enviados; cuerpo vacío o campos no nullable con null: 422. |
| `GET/POST /suppliers/{id}/products` | Listar/registrar producto vinculado a un insumo existente. |
| `PATCH /suppliers/{id}/products/{product_id}` | Editar/desactivar; identidad y formato usados por precios/sugerencias quedan protegidos. |
| `GET /suppliers/{id}/scrape-runs` | Corridas persistidas en orden descendente de id. |

Desactivar con `{"is_active":false}`. Se preservan proveedores, productos y referencias históricas. `0005` permite registrar una nueva versión activa bajo la misma URL; dos versiones activas del mismo proveedor/URL producen 409. Edición de la identidad usada por quotes/sugerencias continúa bloqueada. Las órdenes antiguas reciben su formato original. No hay borrado físico.

`0004_scrape_config` añade una columna JSONB **opcional** a `supplier_products`; no reemplaza tablas ni reescribe las migraciones anteriores. Contiene parámetros que ya utiliza el normalizador:

```json
{
  "item_id": 1,
  "name_on_site": "Pechuga Pollo Asada Receta del Abuelo Corte Pluma 125 g",
  "url": "https://www.jumbo.cl/pechuga-pollo-asada-corte-pluma-rda-125g-1961592/p",
  "pack_quantity": "0.125",
  "min_packs": 1,
  "scrape_config": {
    "sku": "125608",
    "brand": "Receta del Abuelo",
    "expected_name_tokens": ["pech", "pollo", "asada", "receta", "abuelo"],
    "pack": {
      "commercial_format": "paquete 125 g",
      "content_quantity": "125",
      "content_unit": "g",
      "units_per_pack": 1,
      "base_unit": "kg",
      "is_variable_weight": false
    }
  }
}
```

Ejemplo fechado de la captura E2; confirmar producto y condiciones antes de configurarlo operacionalmente. La cantidad normalizada de `pack` debe coincidir con `pack_quantity` y la unidad del insumo. El adapter se registra en el proveedor (`central`, `jumbo`, `santa_isabel`). Productos antiguos sin `scrape_config` siguen existiendo; refrescarlos registra `SCRAPE_CONFIG_REQUIRED` y conserva precios históricos, sin inventar SKU, formato ni precio.

## Precios

- `GET /prices/{item_id}` devuelve ofertas persistidas, precio publicado con IVA, cantidad requerida y comparación. Muestra `MISSING`, `CURRENT`, `EXPIRED`, `OUT_OF_STOCK`, `INACTIVE` o `FUTURE_OBSERVATION` según el dato disponible.
- `POST /prices/{item_id}/refresh` consulta únicamente las páginas públicas de los adaptadores existentes; respeta robots.txt, bloqueos y pausas de dos segundos. API y CLI comparten bloqueo y temporización. Un worker simultáneo recibe 409. Una falla de fuente no invalida las otras.
- `python -m scraper.postgres --item-id 1` o `--all-active` usa la misma persistencia. Cron diario definido en `render.yaml`, sin ejecución remota acreditada. Exit codes 0/1/2/3 documentados en operación.
- Se conservan `observed_at` y `valid_until = observed_at + 7 días`, incluyendo reintentos. La misma observación se reutiliza; un resultado contradictorio se registra como `QUOTE_CONFLICT`. No se renueva la vigencia de un precio porque su fuente falle.
- Una observación posterior de falta de disponibilidad impide reutilizar una oferta anterior disponible. Los errores posteriores no la reactivan.
- Comparación por costo de formatos completos y mínimo de formatos, sin despacho. Requiere dos **proveedores distintos** vigentes; varios formatos de uno no cumplen ese requisito. El siguiente mejor costo corresponde a otro proveedor.
- Una necesidad calculada con decimales recurrentes se redondea hacia arriba a seis decimales antes de evaluar formatos. Sin consumo calculable, cantidad y recomendación permanecen no calculables.
- Capturas HTML temporales tienen nombres únicos por URL/instante y se eliminan al cerrar el worker. Cada quote conserva un snapshot comercial mínimo (RawQuote, configuración pública, resultado normalizado, fecha y hash del HTTP) y SHA-256 canónico del snapshot. Persiste en PostgreSQL y sus backups durante toda la vida del histórico, sin almacenar scripts/cookies/tokens del proveedor ni exponer capturas por API. Cada corrida conserva ids/timestamps/códigos; un crash se cierra como `WORKER_INTERRUPTED` al recuperar el bloqueo compartido.
- Tres HTML históricos de Central contienen copias redactadas para publicación: GitHub detectó cadenas AWS y se retiraron también tokens del proveedor ajenos a la oferta. Sus metadatos conservan `sha256` del HTTP original y agregan `artifact_sha256` de los bytes publicables y una declaración de redacción. El replay verifica el artifact y conserva la identidad/hash de la adquisición original; el parser entrega idénticos datos comerciales. Los originales completos se conservan únicamente en el respaldo privado y la rama local de recuperación. Fechas, precios, datasets y `observed_prices.json` permanecen intactos.
- No existe carga manual de precios. Con tránsito, `required_quantity` de la acción es cero y la comparación devuelve `IN_TRANSIT`, sin recomendación. La necesidad teórica permanece en `assessment.required_quantity`.

## Sugerencias y decisiones

| Ruta | Contrato |
|---|---|
| `POST /suggestions` | ADMIN; `{"item_id":1}`. Devuelve 201 con snapshot persistido PENDING. Reintento sobre un pendiente válido devuelve el mismo id. |
| `GET /suggestions?item_id=1` | Historial, id descendente; filtro opcional. |
| `GET /suggestions/{id}` | Snapshot, cantidades, proveedor/producto/formatos/costo/alternativa/ahorro y auditoría de decisión. |
| `POST /suggestions/{id}/decision` | ADMIN; `{"decision":"APPROVE"}` o `{"decision":"REJECT","reason":"motivo"}`. |

Con historia insuficiente, sin necesidad, menos de dos proveedores vigentes o una orden en tránsito: 409 y ninguna nueva sugerencia/orden. No se pueden editar cantidad/proveedor (C11). Sólo PENDING → APPROVED/REJECTED; reintento idéntico devuelve la decisión original, sin sustituir autor/fecha. Otra decisión/motivo: 409. Rechazo exige motivo no blanco. Aprobación crea una sola OC interna en la misma transacción y cero entradas de inventario. Nunca envía una compra al proveedor.

La aprobación valida que stock/consumo/umbral/plazo y las dos ofertas utilizadas sigan siendo compatibles. Si cambian, devuelve `STALE_SUGGESTION`, `STALE_PRICES` o `STALE_FORMAT`; el administrador rechaza con motivo y vuelve a generar. No se reescribe el snapshot ni se renueva un precio vencido. Bloqueos: insumo → sugerencia/orden, compartidos con inventario/recepción. Creación concurrente: una PENDING por insumo.

Proyección: `P=S−(W/7)×L`, puede ser negativa. Necesidad `Q=max(0,2W−P−T)`; acción cero con T>0. Con S=1, W=7, L=4, T=0: P=−3, Q=17. El stock físico sigue siendo no negativo.

Conteos POST/GET incluyen `needs_review = difference > 0` y `created_by` (id); movimientos incluyen `{id,name}`. Ambas rutas de alertas incluyen `base_unit`.

## Órdenes y recepción

- `GET /orders` lista órdenes persistidas por id; enlaza sugerencia, producto, cantidades, decisión, estado, fechas y movimiento recibido. Sin paginación/filtros nuevos.
- `POST /orders/{id}/receipt` recibe una orden realmente persistida y aprobada, cuya sugerencia también debe estar aprobada. Nunca acepta una aprobación suministrada por el cliente.
- Para formatos fijos, cuerpo omitido o `{}`: recibe la cantidad completa aprobada. `received_at` es opcional; por defecto usa el momento de registro. Debe incluir zona y estar entre aprobación y registro.
- Para formatos configurados como peso variable, `{"received_quantity":"4.82"}` registra el peso total medido en la unidad base. La cantidad nominal aprobada se conserva; `received_quantity` se obtiene del `ENTRY` enlazado. Recepción parcial de formatos fijos no admitida.
- Una transacción bloquea primero el insumo, después la orden, y confirma juntos `ENTRY`, stock y estado `RECEIVED`. Rollback de cualquiera conserva el estado anterior. Solicitudes simultáneas/reintentos no duplican stock; datos incompatibles con una recepción existente producen 409.
- Un movimiento enlazado a recepción no se invalida mediante las rutas genéricas: 409 `ORDER_RECEIPT_IMMUTABLE`. Se preservan D1 y la coherencia de la orden; un flujo de devolución/conciliación debe acordarse antes de implementarse.
- El puente E2 con lotes, producción y vida útil se conserva para sus pruebas. Esos campos no se convierten en obligatorios en la API del modelo congelado.

## Integración con el trabajo de Pedro

La sugerencia conserva producto, formatos y cantidad al aprobarse; `orders.suggestion_id` enlaza esa sugerencia. La recepción descuenta el tránsito al pasar a RECEIVED, sin editar snapshot/decisión. `0005` exige ambos campos de recepción presentes en RECEIVED y ambos ausentes en APPROVED. El actor de recepción se conserva en su ENTRY.

## Métricas y errores

`GET /metrics?start=<fecha con zona>&end=<fecha con zona>`: ADMIN, intervalo `[start,end)`, lectura consistente REPEATABLE READ. Órdenes aprobadas y ahorro congelado se atribuyen por `decided_at`, sin duplicar reintentos; ahorro respecto del siguiente mejor costo factible, no frente a proveedor habitual. Merma RN-16 se entrega por insumo/unidad. Quiebres observados son transiciones positivas→cero del historial efectivo; demanda perdida no registrada no puede medirse. Alertas atendidas antes del quiebre cuentan aprobaciones con `decided_at < depletion_at` del snapshot; no afirma que la entrega evitó el quiebre. Devuelve ids trazables y definiciones. Sin denominador/previsión: null. Sin línea base/protocolo: impacto E3 `NOT_VERIFIABLE`, sin inventar 30/20/5.

Errores de negocio/validación/HTTP/infraestructura/inesperados usan `{error:{code,message,details?}}`. Un error inesperado es sanitizado y lleva `X-Request-ID`. Base no disponible: 503; `/health` y `/ready` tienen contrato de salud `{status:"unavailable"}`. No se expone SQL/traceback/credenciales. CORS usa orígenes explícitos, sin credenciales/cookies.

Dependencia externa de integración: consumir login/Bearer, manejar 401/403/409/429 y los campos añadidos. El frontend no fue modificado. La aceptación humana y la verificación remota están separadas de los tests técnicos.

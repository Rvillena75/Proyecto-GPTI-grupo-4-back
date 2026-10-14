# Contrato de integración — 12/10/2026

Fuentes: `PLAN_BACKEND_STOCKSMART.md`, precisiones aceptadas de `Arquitectura.md` v1.2 y `docs/decisions.md`, contratos `RawQuote`, `PackSpec` y recepción E2. Backend únicamente. Métricas E3 no implementadas. El contrato completo y los tipos están en `/openapi.json`.

## Responsabilidades y estado

- Rodrigo: proveedores/productos, persistencia de precios, recepción y `GET /orders`, implementados en esta rama.
- Pedro: modelo `0003`, inventario y alertas C1/C5/C12 integrados; sugerencias, aprobación/rechazo y JWT aún sin rutas en el código disponible; Blueprint Render conservado, servicio/URL sin verificar.
- El flujo completo necesita los módulos pendientes de Pedro. Las órdenes de las pruebas se preparan directamente en una base aislada; esto no sustituye una aprobación HTTP real.
- Las rutas actuales no requieren autorización. Pedro debe integrar JWT y permisos previstos (ADMIN/WAREHOUSE) antes de usar el flujo con datos operacionales.

## Proveedores y productos

| Ruta | Comportamiento |
|---|---|
| `GET/POST /suppliers` | Listar o crear proveedor; nombre único, plazo no negativo. |
| `GET/PATCH /suppliers/{id}` | Consultar/editar solo los campos enviados; cuerpo vacío o campos no nullable con null: 422. |
| `GET/POST /suppliers/{id}/products` | Listar/registrar producto vinculado a un insumo existente. |
| `PATCH /suppliers/{id}/products/{product_id}` | Editar/desactivar; identidad y formato usados por precios/sugerencias quedan protegidos. |
| `GET /suppliers/{id}/scrape-runs` | Corridas persistidas en orden descendente de id. |

Desactivar con `{"is_active":false}`. Se preservan proveedores, productos y sus referencias históricas; el contrato no incluye borrado físico. Para cambiar URL/formato/configuración después de tener precios u órdenes, desactivar y registrar otro producto. Los insumos conservan `POST/GET/PATCH /items`; su unidad e historial permanecen inmutables.

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
- `python -m scraper.postgres --item-id 1` usa la misma persistencia; debe ejecutarse con `STOCKSMART_DATABASE_URL`. La programación diaria en Render corresponde a Pedro y no se configura desde esta intervención.
- Se conservan `observed_at` y `valid_until = observed_at + 7 días`, incluyendo reintentos. La misma observación se reutiliza; un resultado contradictorio se registra como `QUOTE_CONFLICT`. No se renueva la vigencia de un precio porque su fuente falle.
- Una observación posterior de falta de disponibilidad impide reutilizar una oferta anterior disponible. Los errores posteriores no la reactivan.
- Comparación por costo de formatos completos y mínimo de formatos, sin despacho. Requiere dos **proveedores distintos** vigentes; varios formatos de uno no cumplen ese requisito. El siguiente mejor costo corresponde a otro proveedor.
- Una necesidad calculada con decimales recurrentes se redondea hacia arriba a seis decimales antes de evaluar formatos. Sin consumo calculable, cantidad y recomendación permanecen no calculables.
- Capturas públicas y hashes se guardan fuera de Git, en `STOCKSMART_PRICE_EVIDENCE_DIR` o en la carpeta temporal predeterminada. Cada corrida conserva estado, ids de productos, timestamps, hash de evidencia y códigos de fallo en `scrape_runs.message`.
- Tres HTML históricos de Central contienen copias redactadas para publicación: GitHub detectó cadenas AWS y se retiraron también tokens del proveedor ajenos a la oferta. Sus metadatos conservan `sha256` del HTTP original y agregan `artifact_sha256` de los bytes publicables y una declaración de redacción. El replay verifica el artifact y conserva la identidad/hash de la adquisición original; el parser entrega idénticos datos comerciales. Los originales completos se conservan únicamente en el respaldo privado y la rama local de recuperación. Fechas, precios, datasets y `observed_prices.json` permanecen intactos.
- No se agrega carga manual de precios ni API de métricas.

## Órdenes y recepción

- `GET /orders` lista órdenes persistidas por id; enlaza sugerencia, producto, cantidades, decisión, estado, fechas y movimiento recibido. Sin paginación/filtros nuevos.
- `POST /orders/{id}/receipt` recibe una orden realmente persistida y aprobada, cuya sugerencia también debe estar aprobada. Nunca acepta una aprobación suministrada por el cliente.
- Para formatos fijos, cuerpo omitido o `{}`: recibe la cantidad completa aprobada. `received_at` es opcional; por defecto usa el momento de registro. Debe incluir zona y estar entre aprobación y registro.
- Para formatos configurados como peso variable, `{"received_quantity":"4.82"}` registra el peso total medido en la unidad base. La cantidad nominal aprobada se conserva; `received_quantity` se obtiene del `ENTRY` enlazado. Recepción parcial de formatos fijos no admitida.
- Una transacción bloquea primero el insumo, después la orden, y confirma juntos `ENTRY`, stock y estado `RECEIVED`. Rollback de cualquiera conserva el estado anterior. Solicitudes simultáneas/reintentos no duplican stock; datos incompatibles con una recepción existente producen 409.
- Un movimiento enlazado a recepción no se invalida mediante las rutas genéricas: 409 `ORDER_RECEIPT_IMMUTABLE`. Se preservan D1 y la coherencia de la orden; un flujo de devolución/conciliación debe acordarse antes de implementarse.
- El puente E2 con lotes, producción y vida útil se conserva para sus pruebas. Esos campos no se convierten en obligatorios en la API del modelo congelado.

## Integración con el trabajo de Pedro

La sugerencia debe conservar su producto, formatos y cantidad (`supplier_product_id`, `packs`, `purchased_qty`) al aprobarse; `orders.suggestion_id` enlaza esa sugerencia. La recepción descuenta el tránsito al cambiar a `RECEIVED`, sin editar la sugerencia ni la decisión. Estos contratos tienen pruebas con PostgreSQL; faltan las rutas de generación/decisión y JWT que los produzcan y autoricen.

No se modifica `render.yaml` ni producción. Incorporar el PR, configurar productos, conectar auth y crear/verificar el servicio son tareas diferenciadas. Validación técnica no equivale a aceptación humana.

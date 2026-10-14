> **Cierre metodológico vigente —07/10/2026.** [Cierre E2/E3](<../../../Entrega 2/docs/cierre_metodologico_E2.md>). Los KPI describen una realización sintética. Las afirmaciones anteriores de aptitud/inaptitud como línea base experimental quedan superadas por este cierre: E2 se acepta por funcionamiento y reproducibilidad;30/20/5 se evaluará en la simulación comparativa E3 futura. Huevos permanece descartado por decisión del equipo y se conserva como fixture técnico histórico.

# Componentes E2 de Rodrigo — corte histórico 07/10/2026

**Integración 12/10/2026:** worker público y reglas de formatos reutilizados desde la API, precios/corridas persistidos en el modelo de Pedro, proveedores/productos y recepción/historial implementados. Contrato vigente: [INTEGRACION_BACKEND.md](INTEGRACION_BACKEND.md). Las notas inferiores conservan el estado histórico E2; `forecast.py` ahora incluye C1/C5/C12 y su contexto ORM. El puente E2 de lotes/vida útil sigue disponible para los fixtures; la API de órdenes no exige esos campos sin una decisión acordada. Sugerencias, decisión y autenticación siguen pendientes de Pedro.

Insumo: pechuga de pollo asada refrigerada Receta del Abuelo, base kg, pieza nominal 2,5 kg por peso y paquetes 125 g. Vida efectiva 5 días de categoría desde primera apertura/corte; cerrada 70 referencia de catálogo, etiqueta real no verificada. No es pollo crudo. Huevos queda fixture técnico histórico merma 0, descartado históricamente por decisión del equipo; no define los criterios funcionales actuales de E2.

Desde `backend/`, tras `python -m pip install -e '.[test]'`:

```powershell
python -m app.e2.dataset --config tests/fixtures/e2/config/dataset_v2.json --out outputs/dataset_v2
python -m app.e2.validation --dataset outputs/dataset_v2
python -m scraper.pipeline --config tests/fixtures/e2/config/products.json --out outputs/prices --mode live --required-quantity 2.5
python -m scraper.pipeline --config tests/fixtures/e2/config/products.json --out outputs/prices_replay --mode replay --evidence tests/fixtures/e2/evidencia/2026-10-07/asada --at '2026-10-07T01:07:00-03:00' --required-quantity 2.5
python -m pytest -q
ruff check app scraper tests migrations
ruff format --check app scraper tests migrations
```

Pytest requiere `STOCKSMART_TEST_DATABASE_URL` dedicada `_test`; recrea supublic. Replay `python -m app.e2.replay --dataset outputs/dataset_v2 --out outputs/replay.json` usa otra base `_test` vacía, no borra inventarios existentes. Tests históricos de huevos usan `tests/fixtures/e2_huevos`, conservados sin cambiar sus series.

Manual cada 5 días 1 pieza, estimada exclusivamente de 7 días previos sintéticos 3,075 kg, redondeo cercano; Poisson y pesos posteriores semillas independientes. Merma emerge de remanentes por apertura 5 días/FEFO, no tasa fija. Fixture produce 31,8 kg de demanda,5,207 merma 13,9295 %,0 quiebres,0,374 final; ningún reajuste después de los KPI. Resultados observados en una realización sintética reproducible del Dataset v2. Cada KPI es un resultado calculado del Dataset v2, útil para comprobar funcionamiento y trazabilidad. No constituye resultado real del negocio, línea base empírica, línea base experimental definitiva, desempeño real de StockSmart ni prueba de cumplimiento de 30/20/5. La aceptación funcional de E2 no exige que la realización canónica contenga todos los fenómenos: quiebre 0 % se conserva y los casos de quiebre se prueban con fixtures separados. La merma 13,93 % se conserva sin demostrar impacto. Las cifras 15,7 % y 7 % son referencias provenientes de E1, no objetivos de calibración del Dataset v2.

Puertos propios: RawQuote → normalize → latest.jsonquotes/runs, PackSpec.is_variable_weight, ApprovedOrder → ReceiptService.receive. Peso realobligatorio enkg; para múltiples piezas, piece_quantities suma altotal. PatchOC agrega received_quantity envariable. Resultado lotecontiene cerrado/piezas/vida abierta; recepción no abre. Callback usa misma sesión, no commit separado. Se preservanlocks/idempotencia/rechazoOC/validación temporal/rollback; inventario original sólo conserva patch transaccional mínimo aceptadoantes, no cambionuevo.

`assess_replenishment` mantiene≤/calendario/máximo activo/OCen tránsito; forecast.py original intacto ystock seguro 28 días/4 respaldado v1.1§4.3. Endpoints Pedro aún L=0 / T=0. Pedro debe confirmar/mapeardefinitivosORM de precios/runs/proveedores/OC/lotes/callback/fechasdeapertura y contexto de alertas. Esta guía no afirmaAPI conectada ni duplicatablasrutas. Declaración IA §13 conservada, ampliación fiel en registrodelworkspace.

Ver fuera delcheckout `Entrega 2/docs/seleccion_insumo_E2.md`, informe, contratos yfuentes; el backend funciona autónomo con fixtures. 173 pytest/22 históricos offline, replay PostgreSQL, Ruff/Alembic y hashesverificados. Sin commit/push. **SIMULACIÓN E3 — NO IMPLEMENTADA POR DECISIÓN DEL EQUIPO.**


Dataset v2 y simulación E3 son artefactos distintos. Dataset v2 queda congelado para E2; la medición de 30/20/5 se realizará posteriormente mediante la simulación comparativa de E3.

Fixtures de prueba adicionales: `tests/unit/test_e2_functional_fixtures.py`; sus entradas controladas no alteran el dataset canónico.

# Comparación sintética de políticas E3

## Alcance y escenarios

Se ejecutaron dos políticas sobre la **misma demanda diaria dentro de cada escenario** y el mismo stock inicial, vencimiento, despachos y conjunto de proveedores. El modelo es sintético y no representa un ensayo en un negocio. Las observaciones de precio del 07/10/2026 se usaron como precios fijos supuestos incluso antes de esa fecha; no son precios históricos del período. La factibilidad de compra y los mínimos comerciales no están verificados.

- **Original:** demanda congelada de Dataset v2, sin alterarla. La política manual se reproduce contra los 70 saldos y mermas diarios del dataset; la prueba falla si hay una diferencia.
- **Estrés:** para cada día y receta se multiplica por 1,30 el número sintético de servicios y se redondea al entero más cercano (mitades hacia arriba). Esto produce 41,775 kg frente a 31,800 kg, un aumento efectivo de **31,37 %** por el redondeo. Se definió a solicitud del equipo después de conocer que el original tenía cero quiebres: **es un experimento exploratorio seleccionado, no una validación independiente ni una tasa observada en un restaurante**.

En ambos escenarios la base manual compra una pieza nominal de 2,5 kg a Central cada cinco días, con un día de entrega. La política StockSmart simula evaluación diaria a las 21 h usando las funciones P4/P5 del backend, consumo de días completos previos, plazo máximo activo, tránsito y formatos enteros. Se asume que una persona aprueba inmediatamente cada sugerencia; la aplicación no compra automáticamente. Se aplican FEFO y cinco días de vida tras apertura, las mismas reglas de vencimiento, demanda y costos de despacho por pedido. Los pesos por fecha son determinísticos y no dependen de la demanda futura.

## Resultados de esta ejecución

| Indicador | Original manual | Original StockSmart | Estrés manual | Estrés StockSmart |
|---|---:|---:|---:|---:|
| Días con stock cero al cierre | 0 | 0 | 13 | 0 |
| Días con demanda no atendida | 0 | 0 | 13 | 0 |
| Demanda no atendida (kg) | 0 | 0 | 5,311 | 0 |
| Merma por vencimiento (kg) | 5,207 | 3,861 | 0,917 | 2,248 |
| Pedidos emitidos | 14 | 11 | 14 | 9 |
| Desembolso de pedidos (CLP) | 530.110 | 754.360 | 530.110 | 679.535 |
| Stock final (kg) | 0,374 | 8,040 | 0 | 5,908 |

**Quiebres:** en el original la reducción relativa no es evaluable: la base es cero. En estrés, según el indicador acordado de días con stock cero al cierre, `(13 − 0) / 13 × 100 = 100 %`, superior al umbral del 30 %. Es un resultado del escenario sintético exploratorio, no un cumplimiento demostrado en operación real.

**Merma:** se eligió comparar kilogramos vencidos; el protocolo debe aprobar esta definición y distinguirla de la tasa P3 sobre inventario disponible. En el original `(5,207 − 3,861) / 5,207 × 100 = 25,85 %` de reducción simulada. En estrés, `(0,917 − 2,248) / 0,917 × 100 = −145,15 %`: la merma aumenta y no se alcanza el umbral del 20 %. La cantidad absoluta de demanda no atendida también difiere, por lo que las dos situaciones deben interpretarse en conjunto.

**Ahorro:** se calculó una referencia exploratoria por pedido: comparar el costo seleccionado con la siguiente opción de otro proveedor para la misma cantidad requerida y sumar esos importes. El proxy ponderado es **3,10 %** en el original y **21,86 %** en estrés. No acredita la meta de ahorro porque la elegibilidad, monto mínimo, logística, equiparabilidad comercial y precios históricos no están validados; además el segundo proveedor más barato es un comparador de opciones, no necesariamente la compra habitual real. No se presenta la diferencia de desembolso como ahorro neto: las políticas satisfacen cantidades distintas y terminan con stocks distintos.

## Reproducción

Desde la raíz del repositorio, con las dependencias existentes del backend:

```powershell
python simulacion/e3/comparar_politicas.py
python -m pytest simulacion/e3/tests -q
```

El simulador usa `dataset_v2/` y `parametros_productos.json` de esta carpeta. La comparación se ejecuta en memoria y no lee ni modifica PostgreSQL; la base local montada por `entorno_simulacion.py` es un entorno separado para comprobar la API y los parámetros. El archivo de resultado por defecto queda en `resultados_locales/` y no se publica automáticamente. `evidencia/comparacion_politicas_2026-11-15.json` conserva los registros diarios, pedidos y resultados de referencia, incluyendo los supuestos.

La aprobación del protocolo y una validación con datos y condiciones comerciales del caso real siguen pendientes. El resultado no acredita simultáneamente 30/20/5 ni justifica afirmar que StockSmart logró esas metas en una empresa.

# Exploración de políticas para reducir quiebres y merma

**Revisión posterior:** ver [DECISIONES_VALIDACION.md](DECISIONES_VALIDACION.md). La revisión comercial detectó pedidos incompatibles con límites publicados y la validación adicional mostró insuficiente robustez. Los resultados de esta exploración se conservan como evidencia histórica; no acreditan ahorro ni adopción de la candidata.

## Estado y preservación

La simulación anterior permanece congelada en el commit `aef2a42d2e4ba608c5c7ee5a71777b6edb05895e` y en la rama `simulacion/e3-despachos-20261115`. Los archivos de Dataset v2 y su comparación anterior no fueron modificados. Este análisis agrega políticas hipotéticas en una rama separada; **no modifica ni acredita el comportamiento actual del backend**.

La exploración utiliza el mismo dataset y la misma demanda para todas las políticas de cada escenario. Se probaron 24 variantes: dos estrategias de selección de proveedor y 12 combinaciones de reserva (1, 2 o 3 días) y cobertura (2 a 6 días, siempre mayor que la reserva). Se ejecutaron con los factores 1,00; 1,15; 1,30 y 1,45 sobre los servicios diarios, redondeados a enteros. Todos los factores comparten el mismo dataset: son pruebas de sensibilidad, no muestras independientes. La cuadrícula y la elección de una candidata se realizaron **después de observar resultados anteriores**; sus resultados son exploratorios y están sujetos a selección.

## Una candidata para estudiar

La variante `precio_minimo_b1_c5` revisa diariamente el consumo de días anteriores, excluye del inventario proyectado los lotes cuyo vencimiento conocido ocurre antes de la llegada estimada y solicita una compra cuando la proyección baja a una reserva equivalente a un día de consumo. Busca cubrir cinco días después de la llegada y selecciona la opción de menor costo nominal, considerando formatos completos y despacho. El cálculo usa el plazo máximo entre proveedores elegibles y se asume aprobación humana inmediata. **Es una regla nueva simulada, no una función que ya exista en StockSmart.**

Bajo el escenario exploratorio de estrés 1,30, la demanda común sube de 31,800 a 41,775 kg tras redondeos (+31,37 %). Los resultados son:

| Indicador | Manual | StockSmart anterior | Candidata experimental |
|---|---:|---:|---:|
| Días con stock cero al cierre | 13 | 0 | 0 |
| Demanda no atendida (kg) | 5,311 | 0 | 0 |
| Merma por vencimiento (kg) | 0,917 | 2,248 | 0,304 |
| Compras emitidas | 14 | 9 | 16 |
| Desembolso por pedidos (CLP) | 530.110 | 679.535 | 875.250 |
| Stock final (kg) | 0 | 5,908 | 3,335 |

Con la referencia manual, la reducción relativa de quiebres es `(13−0)/13 = 100 %` y la de merma es `(0,917−0,304)/0,917 = 66,85 %`. Por tanto, **los dos umbrales físicos se satisfacen numéricamente en este escenario sintético y para esta candidata seleccionada**. Respecto de la política StockSmart anterior, la merma cae de 2,248 a 0,304 kg, pero el desembolso de pedidos sube en $195.715 (28,80 %); no es una evaluación económica comparable ajustada por inventario, momentos de compra o servicio.

El proxy de ahorro de proveedor es `(941.690−875.250)/941.690 = 7,06 %`: suma, para cada necesidad, la siguiente oferta de otro proveedor y la compara con la opción elegida. **No acredita la meta del 5 %**: los precios del 07/10 se usan como supuestos fijos para todo el período anterior, no se verificó la elegibilidad ni los mínimos de compra, el costo de Central es un proxy, se comparan formatos y cantidades compradas diferentes, y la segunda oferta no representa necesariamente una compra habitual viable. El gasto de la candidata supera en $345.140 al de la base manual, que también deja demanda sin atender. No se debe llamar ahorro neto a esta diferencia.

## Sensibilidad de la misma candidata

| Factor nominal de servicios | Quiebres manual → candidata | Merma manual → candidata (kg) | Proxy de proveedor candidata |
|---|---:|---:|---:|
| 1,00 | 0 → 0; reducción no evaluable | 5,207 → 5,239 | 17,93 % |
| 1,15 | 4 → 0 | 2,391 → 2,482 | 17,34 % |
| 1,30 | 13 → 0 | 0,917 → 0,304 | 7,06 % |
| 1,45 | 20 → 0 | 0,480 → 0 | 0,59 % |

La candidata no alcanza simultáneamente los tres umbrales numéricos en todos los factores: con 1,15 la merma aumenta; con 1,45 el proxy de proveedor es inferior a 5 %. En el caso original no se puede dividir por cero para obtener una reducción relativa de quiebres. El escenario 1,30 ya había sido escogido deliberadamente para producir quiebres, por lo que no sirve como validación externa de la selección.

Los precios, cobertura, plazos y despachos son supuestos, no condiciones comerciales comprobadas. La demanda no atendida se considera venta perdida; no se asigna un costo de pérdida de ventas, ni se valora el stock final, ni se descuentan compras fuera del horizonte. Mantener ambos indicadores de servicio y la posición de inventario junto al gasto evita interpretar como ahorro una política que simplemente vende menos o acumula existencias.

## Reproducir

Desde la raíz del repositorio con las dependencias del backend instaladas:

```powershell
python simulacion/e3/explorar_politicas.py
python -m pytest simulacion/e3/tests -q
```

La ejecución en memoria no modifica PostgreSQL ni el dataset. Genera el detalle completo y el resumen en `simulacion/e3/resultados_locales/`, carpeta excluida de Git. Se conserva un resumen con las 24 alternativas por escenario y el detalle diario de la candidata en `evidencia/resumen_exploracion_merma_2026-11-15.json`; ese archivo incluye el SHA-256 canónico del resultado completo. El código permite regenerar el resultado completo y comprobar la misma huella. Las referencias de proveedores y precios están en `HALLAZGOS.md`.

## Para tomar una decisión

La candidata merece una revisión del equipo y de Patricio, pero no corresponde declarar cumplidas las metas completas. Falta acordar formalmente los indicadores, el escenario y el comparador de ahorro; validar precios vigentes, presentaciones, condiciones de despacho, monto mínimo y equivalencia de compra; valorar inventario y ventas no atendidas; contrastar la política con datos independientes; y, si se adopta, implementarla y probarla en StockSmart. El dataset v2 original sigue congelado.

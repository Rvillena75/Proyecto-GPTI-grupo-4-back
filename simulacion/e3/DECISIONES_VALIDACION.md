# Decisiones y validación posterior de la candidata

## Estado de cierre

Rodrigo aceptó en esta conversación el uso de los escenarios original y de estrés para esta entrega. Esta decisión no equivale a una aprobación de Patricio ni a un compromiso de que las metas estén cumplidas. El dataset original, la comparación anterior y la cuadrícula exploratoria se conservan con sus resultados.

Se completó una revisión comercial y una validación adicional de la candidata fija `precio_minimo_b1_c5`. **El resultado es negativo para acreditar el 5 % de ahorro y no justifica adoptar la candidata como regla general del producto.** La candidata sigue siendo un experimento. Su implementación en el backend queda sujeta a una decisión explícita de adopción; no se cambió P4/P5 en esta revisión.

## Revisión comercial

Consultas realizadas el 15/11/2026, sin transacciones, ingreso de cuentas ni checkout:

- Jumbo: la página del producto identifica pechuga asada RDA de 125 g y muestra $2.530 ($20.240/kg). Es una observación de página, no confirma stock, dirección, elegibilidad de una compra comercial ni precios de julio–octubre. Sus términos establecen límite de 1 kg para quesos y fiambres, mínimo según localidad entre $19.990 y $50.000 y tarifas según localidad. Fuente: https://www.jumbo.cl/pechuga-pollo-asada-corte-pluma-rda-125g-1961592/p y https://www.jumbo.cl/informaciones-legales
- Santa Isabel: sus términos establecen límite de 1 kg para quesos y fiambres, mínimo de despacho de $19.990 y costo publicado de $3.990 sujeto a la compra. La ficha del producto requiere JavaScript en el acceso utilizado y no permitió confirmar un precio actual. Fuente: https://www.santaisabel.cl/informaciones-legales y https://www.santaisabel.cl/pechuga-pollo-asada-corte-pluma-rda-125g-1961592/p
- Central Mayorista: no se logró abrir la ficha del producto en esta revisión. Una publicación oficial de Walmart para Temuco informa despacho gratuito sobre $300.000 y retiro desde $150.000. Esa referencia regional no confirma condiciones para RM. Los $3.990 imputados en la simulación siguen siendo un proxy logístico, sin confirmación del proveedor. Fuente: https://www.walmartchile.cl/central-mayorista-llega-a-temuco-abasteciendo-a-13-comunas-de-la-region-de-la-araucania/

El filtro de `validar_candidata.py` detectó que **12 de 16 pedidos de la candidata del estrés elegido superan 1 kg en los canales minoristas**. Los otros cuatro son de Central, con montos inferiores a la referencia publicada para despacho de Temuco y sin cotización RM. Ninguna de las 16 órdenes tiene factibilidad verificada. No se eluden límites separando órdenes o cuentas: eso cambiaría restricciones, tarifas y operación.

El 7,06 % anterior queda como resultado histórico de un comparador hipotético. **No debe presentarse como ahorro comercial validado.** Para obtener un comparador viable faltan una modalidad/dirección de compra y una cotización que acepte los volúmenes, o un acuerdo B2B documentado. Agregar otros productos a la canasta solo es admisible si realmente forman parte de la compra y se asignan sus costos de forma consistente a las dos políticas.

## Decisión técnica para comparar costos

Se registran separados servicio, desperdicio, stock final, pedidos en tránsito y desembolso. No se usa el desembolso bruto de políticas con distinta demanda atendida e inventario para declarar ahorro.

Como herramienta de sensibilidad, se implementó:

`C(v,p) = desembolso − v × (stock final + tránsito nominal) + p × demanda no atendida`

Se aplica el mismo valor residual `v` (CLP/kg) y penalización `p` (CLP/kg perdido) en ambas políticas. El inventario inicial es idéntico y su costo compartido se excluye. El tránsito se valora nominalmente porque no se conoce el peso recibido futuro. La fórmula no representa una compra de emergencia ni demuestra que la venta perdida pueda recuperarse. Los valores reales de `v` y `p` siguen sin determinar y el cálculo no se utiliza para acreditar una meta.

En el estrés elegido, para que la candidata resulte al menos un 5 % menor en este costo ajustado:

| Valor residual supuesto | Penalización mínima necesaria por kg no atendido |
|---|---:|
| $0/kg | $73.660/kg |
| $13.550/kg | $64.703/kg |

Los $13.550/kg son una referencia histórica del dataset usada solo para explorar sensibilidad; no acreditan valor de liquidación ni ingreso recuperable. No existe evidencia para las penalizaciones resultantes. Por tanto, la revisión no prueba ahorro del 5 %. Inventar una penalización para superar la meta sería inválido.

## Validación adicional sin reajustar parámetros

Se fijó la candidata antes de ejecutar las nuevas pruebas. Se generaron 30 series de demanda con semillas `2026111600` a `2026111629` por cada uno de dos niveles de demanda: media original y media multiplicada por 1,30. Se usó el generador Poisson de recetas del backend, medias por día de semana 5, 5, 6, 6, 7, 8 y 5 servicios, y 75 g por servicio. Cada política recibe la misma serie dentro de cada réplica. Se usan las mismas semillas entre niveles para facilitar comparaciones emparejadas.

Son realizaciones aleatorias nuevas del mismo modelo sintético, **no nuevos datos observados de un negocio ni una validación externa**. No se reeligió la candidata ni se ajustaron sus parámetros después de ver estas pruebas. Se reportan sumas y razones agregadas; no intervalos de confianza ni garantía de cumplimiento en cada negocio.

| Indicador sobre 30 réplicas | Demanda original | Demanda media por 1,30 |
|---|---:|---:|
| Suma días con cierre cero manual → candidata | 25 → 2 | 356 → 5 |
| Reducción agregada de días de quiebre | 92,00 % | 98,60 % |
| Suma merma manual → candidata (kg) | 152,391 → 142,363 | 27,113 → 20,100 |
| Reducción agregada de merma | 6,58 % | 25,87 % |
| Réplicas que cumplen ambos umbrales físicos | 3/30 | 18/30 |

La candidata reduce quiebres en el agregado, pero no alcanza la reducción de merma del 20 % con demanda original, ni cumple ambos umbrales en todas las réplicas de estrés. No se acepta como solución general robusta ni como demostración de 30/20/5. Sus resultados físicos siguen condicionados a un abastecimiento hipotético cuya factibilidad comercial no se confirmó.

## Reproducción y entregables

```powershell
python simulacion/e3/validar_candidata.py
python -m pytest simulacion/e3/tests -q
```

`evidencia/validacion_candidata.json` conserva las 60 réplicas, exclusiones comerciales y umbrales económicos. `validar_candidata.py` vuelve a generarlos sin red ni base productiva. El módulo verifica restricciones conocidas; nunca certifica elegibilidad sin checkout y cotización.

Las decisiones técnicas de esta revisión son mantener la candidata fija, publicar los resultados desfavorables, preservar los escenarios aceptados y tratar el 5 % como **no acreditado**. La evaluación está implementada y documentada; la meta no está demostrada. Para cerrar como cumplimiento hace falta resolver el abastecimiento real y validar una política que mantenga resultados bajo esas condiciones.

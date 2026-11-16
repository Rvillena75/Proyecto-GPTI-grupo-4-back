# Cierre de decisiones de la evaluación simulada

Fecha: 16/11/2026. Alcance: decisiones delegadas por Rodrigo para completar la evaluación de ahorro y la validación de la candidata. Los resultados siguientes son sintéticos y no prueban cumplimiento en una empresa real.

## Decisiones adoptadas para el modelo

1. Se mantienen aceptados los escenarios original y de estrés ya definidos. El caso congelado y sus resultados no se modifican.
2. Se fija **Macul** como comuna de referencia, siguiendo el antecedente de consulta del repositorio de documentación del proyecto (`GPTI/docs/investigacion_proveedores_y_fuentes_precios.md`, contexto de Central, línea 94). No hace falta una dirección personal para este modelo.
3. Se fija compra **solo de pechuga**, con despacho y necesidad común dentro de dos días corridos. La dirección exacta, el horario y el stock real quedan fuera del modelo; no se finge una cotización ejecutable.
4. Para una comparación de costos de igual cantidad se adopta un pedido de **ocho paquetes de 125 g**, una misma marca y un total de 1 kg. Se usan los precios históricos como supuestos constantes y las tarifas de referencia de la simulación. El mínimo de $19.990 para la referencia minorista es un supuesto del contexto Macul; no se confirmó en checkout. Si aplicara un mínimo de $50.000, este pedido no sería elegible por sí solo. Central se excluye de este comparador por no tener formato equivalente y modalidad de despacho confirmada para ese volumen.
5. No se inventa un contrato B2B ni se supone que los supermercados aceptan compras comerciales por encima de sus límites. Los pedidos de la candidata que infringen condiciones publicadas siguen marcados como no verificados o incompatibles.
6. Se compara el costo del período por **valoración contable de existencias a costo de adquisición**, no a un supuesto precio de reventa. También se conserva el desembolso, la merma, el stock y la demanda atendida por separado.
7. Se decide **no incorporar la candidata al backend operativo**: la validación revela límites de robustez y un mayor costo. Se conserva para investigación reproducible; el código del producto no se presenta como si ejecutara esa política.

Estas son decisiones técnicas autorizadas para el modelo. No sustituyen aprobación docente ni evidencia comercial.

## Resultado del comparador de compra equivalente

| Pedido idéntico de 1 kg | Productos | Despacho supuesto | Total |
|---|---:|---:|---:|
| Jumbo, 8 paquetes | $20.240 | $4.990 | $25.230 |
| Santa Isabel, 8 paquetes | $20.560 | $3.990 | $24.550 |

Ahorro simulado frente a Jumbo: `(25.230 − 24.550) / 25.230 × 100 = 2,70 %`. **La meta del 5 % no se cumple en este comparador de igual cantidad.** El 7,06 % de la exploración anterior no se reemplaza retrospectivamente: se conserva como resultado de otro comparador hipotético, con sus limitaciones.

Los formatos y precios de referencia provienen de las observaciones congeladas del 07/10/2026. Los mínimos y límites publicados se revisaron en el trabajo previo. Una consulta de catálogo no confirma despacho, elegibilidad comercial o aceptación de un pedido. No se extrapolan estos valores a una cotización actual ni a la realidad histórica de julio–octubre.

## Comparación económica con inventario conciliado

Se reconstruyó un libro auxiliar por lote usando las mismas recepciones, pesos, FEFO, aperturas, consumos y vencimientos de cada simulación. Se verificó cada saldo y cada cantidad vencida diaria contra la ejecución física; el libro no cambia las decisiones de compra.

`Costo atribuido = costo de apertura + pedidos pagados − existencias finales a costo de adquisición − productos pagados todavía en tránsito`

La identidad adicional comprobada es:

`Costo atribuido = costo del producto consumido + costo del producto vencido + despachos`

Los despachos se tratan como gasto comprometido por pedido emitido. Las existencias en tránsito se reconocen por el subtotal del producto; no se adivina su peso futuro. En las tres ejecuciones del estrés elegido no hay pedidos finales pendientes. Las cifras contables no incluyen mano de obra, financiación ni margen de ventas perdido. El precio nominal por pieza se conserva conforme al dataset, aunque el peso recibido sintético varíe.

| Política en estrés | Demanda atendida | Valor contable de stock final | Costo atribuido del período |
|---|---:|---:|---:|
| Manual | 36,464 kg | $0 | $563.985,00 |
| StockSmart anterior | 41,775 kg | $80.650,27 | $632.759,73 |
| Candidata experimental | 41,775 kg | $67.567,60 | $841.557,40 |

StockSmart anterior y la candidata atienden la misma cantidad y no pierden demanda en este caso. Tras reconocer el inventario a su costo de adquisición, la candidata sigue siendo aproximadamente **33,00 % más cara**. Esta comparación permite descartar su adopción por ahorro bajo los supuestos del modelo. El total de la política manual no se compara directamente como si tuviera el mismo servicio: deja 5,311 kg sin atender. Los costos unitarios calculados para ella solo describen eficiencia sobre la cantidad efectivamente atendida.

La valoración contable es una convención de análisis, no acredita que pueda venderse todo el stock final ni que su valor comercial sea igual al contable.

## Resultado de validación de la candidata fija

Se conserva la candidata de reserva un día y cobertura cinco días. La validación previa con 60 realizaciones Poisson nuevas, sin reajustar parámetros, mostró:

| Agregado de 30 realizaciones | Demanda original | Demanda media por 1,30 |
|---|---:|---:|
| Reducción de días con cierre cero | 92,00 % | 98,60 % |
| Reducción de merma | 6,58 % | 25,87 % |
| Réplicas que cumplen ambos umbrales físicos | 3/30 | 18/30 |

No se acepta como política general robusta: la demanda original no alcanza el umbral de merma en el agregado. Las realizaciones nuevas pertenecen al mismo modelo sintético; no son nuevos datos de un restaurante. Los resultados siguen condicionados a precios y abastecimiento supuestos.

## Estado definitivo de esta evaluación

- Escenarios y contexto: definidos para la simulación por delegación de Rodrigo.
- Metodología de costos: implementada, conciliada y reproducible.
- Validación de la candidata: ejecutada; resultado insuficiente para su adopción general.
- Meta del 5 %: no cumplida en la referencia equivalente y no acreditada comercialmente.
- Producto: se conserva su regla actual; no se atribuye la candidata experimental al backend.
- Cierre de entrega: se puede presentar la **evaluación completa con sus resultados desfavorables**. No corresponde declarar que se lograron simultáneamente 30/20/5. La aceptación académica depende del equipo y del ayudante.

## Reproducción

```powershell
python simulacion/e3/cerrar_evaluacion.py
python -m pytest simulacion/e3/tests -q
```

La ejecución lee las copias sintéticas del repositorio y no modifica bases de datos. `evidencia/cierre_evaluacion_2026-11-16.json` conserva los resultados completos, sin redondeos monetarios de presentación. `DECISIONES_VALIDACION.md` documenta las fuentes revisadas; `validar_candidata.py` y su evidencia conservan las 60 realizaciones de validación. Los documentos anteriores se mantienen como historia; este cierre resuelve las decisiones de contexto y de adopción que figuraban abiertas.

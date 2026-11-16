# Hallazgos para el protocolo de simulación E3

## Datos y reglas comprobados

- Insumo sintético: pechuga de pollo asada Receta del Abuelo. Período: 27/07/2026 al 04/10/2026, 70 días.
- Reposición manual sintética: 14 compras a Central Mayorista de una pieza nominal de 2,5 kg, cada cinco días corridos, con un día de entrega y sin retraso añadido. El stock de apertura de 2,491 kg procede de una recepción anterior, separada de las 14 entradas.
- Regla de cantidad: consumo sintético previo 3,075 kg / 7 días × 5 días = 2,196 kg; se redondea a una pieza completa de 2,5 kg. La regla no usa el stock observado, alertas ni resultados futuros.
- La realización del dataset tiene 0 días con stock de cierre igual a cero, 0 kg de demanda insatisfecha y 5,207 kg de merma por vencimiento en 11 registros. No es evidencia de comportamiento de un negocio real.
- Con cero quiebres de base no se puede calcular una reducción porcentual de quiebres: el denominador sería cero. Validar con el equipo el indicador alternativo o un experimento adicional sin alterar el dataset congelado.

## Despacho y comparación técnica

Se cargaron $3.990, $4.990 y $3.990 CLP para Central Mayorista, Jumbo y Santa Isabel respectivamente. Son supuestos de un costo por pedido en la RM, no tarifas garantizadas para julio–octubre ni cotizaciones del carrito real.

La aplicación suma despacho una sola vez al subtotal. La ejecución local documentada usó una necesidad de 6,697429 kg y las tres observaciones del 07/10/2026, con reloj histórico al 08/10. Los totales fueron Central $105.615, Jumbo $141.610 y Santa Isabel $142.770. Corresponden a formatos completos diferentes y **no prueban ahorros reales ni factibilidad comercial**.

Para Central se adoptó un proxy logístico hipotético de $3.990, inspirado en una tarifa de otro proveedor; no es un cobro oficial de Central. Central informa despacho sin costo sujeto a un monto mínimo. Jumbo publica $4.990 para comunas de la RM, y Santa Isabel señala $3.990 en sus términos; ambos están sujetos a condiciones, ubicación y checkout. Los mínimos y la logística concreta no están verificados.

## Fuentes

- `dataset_v2/dataset.json` y `dataset_v2/manifest.json`; `parametros_productos.json`; `evidencia/verificacion_local_2026-11-15.json`.
- Central Mayorista, ficha de su aplicación: https://apps.apple.com/es/app/central-mayorista-cl/id6748541267
- Jumbo, información legal, despacho a domicilio: https://www.jumbo.cl/informaciones-legales
- Santa Isabel, información legal y cobertura: https://www.santaisabel.cl/informaciones-legales y https://ayuda.santaisabel.cl/ayuda/despacho-retiro/zonas-cobertura-y-horarios
- Comercializadora CAAN, costo de referencia externo sujeto a sus propias condiciones: https://comercializadoracaan.cl/

Consulta de las fuentes de despacho: 15/11/2026. La asignación del proxy a Central es decisión de simulación, no dato de esa fuente. La comparación definitiva requiere protocolo aprobado, condiciones y costo consistentes entre escenarios, y verificación del equipo en su entorno.

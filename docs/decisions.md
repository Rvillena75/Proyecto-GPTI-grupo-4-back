# Decisiones de implementación del backend

`PLAN_BACKEND_STOCKSMART.md` conserva la autoridad funcional. Estas decisiones precisan P1–P5 para su futura implementación; no modifican las fórmulas ni amplían el alcance del MVP. El núcleo de inventario P1–P5 puede desarrollarse antes de validar las fuentes de P6. Los adaptadores reales de Central Mayorista y Alvi requieren esa validación previa.

## D1 — Reversas y correcciones (P2; efecto en P3)

- Los registros históricos nunca se modifican ni eliminan.
- Una reversa es un nuevo movimiento que referencia al movimiento que corrige y aplica el efecto opuesto sobre el stock. Ambos movimientos se conservan para auditoría.
- Una reversa o corrección tampoco puede dejar el stock negativo.
- El stock inicial solo puede revertirse si no hay movimientos posteriores activos para ese insumo e inventario. Si los hay, se rechaza con `INITIAL_STOCK_HAS_DEPENDENT_MOVEMENTS`; un stock inicial erróneo con historia se corrige mediante un nuevo ajuste por conteo, sin ajuste implícito. Para esta regla, un movimiento posterior activo es uno cuyo efecto no ha sido cancelado por una reversa.
- La reversa de un ajuste por conteo exige simular el historial efectivo sin la contribución del ajuste original y recalcular cronológicamente los saldos desde él, respetando además los puntos de conciliación de D6. Se rechaza la operación completa si algún saldo posterior quedaría negativo; comprobar solo el saldo actual no basta. El movimiento original y su reversa permanecen en el historial de auditoría.
- La reversa invalida el efecto económico del movimiento original para las métricas. Un conteo revertido deja de aportar faltante y ajuste positivo efectivo a P3; una salida revertida deja de aportar consumo a P4. La reversa no constituye una entrada operativa, una salida, un nuevo faltante ni una merma. Los movimientos permanecen visibles en el historial de auditoría.
- Cuando corresponda, el movimiento correcto se registra después de la reversa, como indica P2.
- No se incorporarán mecanismos adicionales complejos de corrección si el MVP no los necesita. Un caso que el plan no defina se documentará antes de convertirlo en comportamiento del sistema.

## D2 — Inicio de historia para consumo y proyección (P4)

- La historia utilizable para calcular consumo comienza cuando existe el stock inicial del insumo.
- Un período completo es un día calendario completo en la zona horaria configurada del establecimiento, delimitado por `[inicio del día local, inicio del día local siguiente)` (normalmente las `00:00` locales). El día en curso se excluye. El cálculo utiliza como máximo los últimos 28 días completos observables; no usa ventanas móviles de 24 horas. Solo los movimientos de salida válidos contribuyen al consumo, conforme a P4.
- El día del stock inicial entra en el promedio solo si el stock inicial ya existía al comenzar ese día. Si se registra a mitad del día, el primer día elegible es el siguiente. Un stock inicial efectivo exactamente al inicio del día cuenta como disponible desde ese inicio.
- Se conserva la distinción entre ausencia de historia suficiente, historia válida sin salidas y consumo positivo. Con menos de siete días de historia el resultado calculable se marca como estimación preliminar; sin al menos un día completo no se calcula el promedio.
- Las fechas se almacenarán de forma consistente y con zona horaria. La zona horaria del local se configura; `America/Santiago` es el valor del piloto, no una constante de la lógica. Los tests harán deterministas los límites temporales, incluidos cambios de horario de verano.

## D3 — Stock seguro configurable (P5)

- Sin valor manual configurado, el stock seguro efectivo es dinámicamente igual al consumo semanal promedio vigente.
- Un valor manual configurado reemplaza al automático.
- Eliminar el valor manual restaura el comportamiento automático.
- Así, una semana de consumo es el comportamiento inicial y se actualiza cuando cambia el promedio. La regla de alerta `stock actual ≤ stock seguro` y el objetivo de dos semanas permanecen como los define P5.
- Sin un día completo de historia y sin umbral manual, el consumo promedio, el stock seguro automático y la cantidad sugerida son no calculables; la alerta tiene estado `INSUFFICIENT_HISTORY`. No se sustituyen por cero, ni se informa falsamente que el stock no está bajo. Si la API expone un booleano de stock bajo, su valor es `null`, no `false`. El stock actual sí se muestra.
- Un umbral manual permite evaluar `stock actual ≤ umbral` aunque el consumo siga sin ser calculable. Si se cumple, la alerta tiene estado `LOW_STOCK` y origen `MANUAL_THRESHOLD`; el consumo y la cantidad sugerida continúan sin calcularse hasta disponer de historia suficiente.

## D4 — Un stock inicial activo por insumo e inventario (P1/P2)

- Existe como máximo un stock inicial activo por insumo y contexto de inventario. Un segundo stock inicial activo se rechaza con `INITIAL_STOCK_ALREADY_EXISTS`; no se convierte en otra entrada.
- Si no hay movimientos posteriores activos, puede revertirse el stock inicial y registrar uno nuevo. Si los hay, la corrección se hace mediante conteo físico, sujeto a la fórmula de P3. Al reemplazar un stock inicial sin movimientos dependientes, el registro anterior y su `observation_started_at` permanecen inmutables; el nuevo stock inicial inicia una nueva observación confiable según D7.

## D5 — Movimientos retroactivos (P2–P4)

- Cada movimiento distingue `occurredAt` (momento efectivo) de `createdAt` (momento de registro), ambos con zona horaria. Los cálculos históricos usan `occurredAt`.
- Se permite registrar un movimiento después de ocurrido si `occurredAt` es igual o posterior al momento efectivo del stock inicial activo y cumple D6. Un movimiento anterior se rechaza.
- Antes de aceptar el movimiento se simula cronológicamente el historial afectado. Si algún saldo posterior queda negativo o se altera una conciliación confirmada según D6, se rechaza la operación completa. La hora de registro no cambia el día al que pertenece el movimiento para P4.
- Los movimientos con el mismo `occurredAt` se ordenan por `createdAt` y luego por `id`. Un movimiento registrado después de un conteo con el mismo `occurredAt` queda lógicamente después de ese conteo; sigue sujeto a todos los conteos posteriores. Este orden se cubre con tests.

## D6 — Conteos físicos como puntos de conciliación histórica (P2/P3)

- Un conteo físico confirmado en el instante `T` constituye un punto de conciliación histórico inmutable. Se conservan exactamente el stock teórico inmediatamente anterior utilizado, el stock físico, la diferencia, el faltante y el ajuste asociados. No se editan, eliminan, recalculan ni se reescriben los saldos ya conciliados para acomodar operaciones creadas después.
- Una operación creada después del conteo, con fecha efectiva anterior a `T`, se rechaza si cambia el stock teórico inmediatamente anterior utilizado por ese conteo. La validación se aplica a **cada** conteo posterior afectado. También se rechaza cualquier reversa retroactiva que invalide una conciliación posterior, aunque los saldos resultantes no sean negativos.
- Un movimiento retroactivo solo es admisible si es posterior al stock inicial, mantiene todas las conciliaciones posteriores y conserva saldos no negativos en toda la secuencia afectada. Si no se puede garantizar alguna condición, se rechaza y se requiere una corrección registrada en el presente.
- Si en `T3` se descubre un error de un movimiento de `T1` que ya fue seguido por un conteo en `T2`, no se cambia `T1` ni se recalcula `T2`. Se registra en `T3` una nueva corrección trazable, referenciada al movimiento erróneo cuando corresponda, con el efecto de stock **necesario desde T3** y sin volver negativo el saldo. Una reversa exacta de D1 solo corresponde si su efecto opuesto es realmente el ajuste necesario; no se aplica a ciegas si el conteo de `T2` ya absorbió el error. Si el ajuste necesario es cero, la corrección deja constancia trazable sin alterar el stock. El conteo histórico permanece igual.
- La corrección en el presente no es un nuevo consumo ni un nuevo faltante por sí misma. Cuando identifica el movimiento original como erróneo, su contribución a métricas derivadas se invalida según D1, sin modificar saldos ni campos de conteos históricos. Si se registra un nuevo conteo físico para corregir el saldo actual, su diferencia y faltante se calculan normalmente según P3.

## D7 — Inicio de observación confiable (P1/P4)

- Para el MVP, el stock inicial no puede registrarse retroactivamente: su `occurredAt` coincide con el momento en que se registra. Ese momento establece `observation_started_at` y el inicio de la historia confiable para P4.
- `observation_started_at` queda inmutable en el registro de stock inicial. No existen días observados antes de ese instante y ninguna ventana de consumo puede incluir tiempo anterior. Si ese stock inicial se revierte y se crea otro conforme a D4, ambos registros conservan su propio instante inmutable; solo el stock inicial activo determina el comienzo de la observación vigente.
- Los primeros días completos posteriores se tratan según P4 y D2: sin un día completo no hay promedio; con menos de siete días el cálculo disponible es preliminar.
- Los movimientos posteriores pueden tener fecha efectiva retroactiva únicamente bajo D5 y D6. La importación de historia anterior al uso de StockSmart queda fuera del MVP.

## Ejemplos mínimos para tests de P1–P5

- Inicial `+10`, salida `-6`, intento de revertir inicial: rechazo `INITIAL_STOCK_HAS_DEPENDENT_MOVEMENTS`.
- Inicial `+10`, ajuste por conteo `+5`, salida `-12`: revertir el ajuste dejaría `-2` en la historia y se rechaza.
- Teórico `20`, físico `15`, faltante `5`: si se revierte ese conteo, su contribución al faltante y a la tasa de merma pasa a `0`; la reversa no cuenta como entrada.
- Stock inicial el 10 de septiembre a las 15:30 locales: el día 10 no cuenta; el 11 es el primer día completo elegible. El día actual tampoco cuenta.
- Sin día completo y sin umbral manual: `INSUFFICIENT_HISTORY`, consumo, stock seguro y cantidad sugerida no calculables. Con stock `7` y umbral manual `10`: alerta por stock bajo de origen `MANUAL_THRESHOLD`, pero cantidad sugerida no calculable.
- Inicial `+10` el 1 de septiembre; salidas `-4` el 3 y `-4` el 5: insertar el 8 una salida efectiva `-5` el 2 se rechaza porque el saldo del 5 sería `-3`.
- **D6.1:** un movimiento retroactivo anterior a un conteo solo se admite si su efecto neto no cambia el stock teórico previo utilizado en ese conteo ni en otro posterior, y toda la secuencia conserva saldo no negativo. Un movimiento aislado con cantidad no nula antes del primer conteo posterior cambia ese saldo y, por tanto, se rechaza; esta regla no exige crear una operación por lotes para el MVP.
- **D6.2:** inicial `10`; conteo posterior con teórico `10` y físico `10`; intentar insertar antes del conteo una salida `-1` se rechaza aunque el saldo resultante sería `9`, porque alteraría su teórico previo.
- **D6.3:** inicial `10`; conteo `+5` que deja `15`; otro conteo físico `14` con ajuste `-1`. Revertir retroactivamente el `+5` se rechaza: el segundo conteo tendría teórico previo `10` en vez de `15`, aunque ningún saldo sea negativo.
- **D6.4:** inicial `10`; salida errónea `-5` en `T1`; conteo físico `10` en `T2` que ajusta `+5`. En `T3`, corregir el error de `T1` deja constancia y no suma otros `5`: el saldo sigue en `10`. Una corrección posterior con efecto de stock no nulo también se admite si el saldo actual y todas las invariantes permanecen válidos; no modifica los registros ni saldos conciliados antes de ella.
- **D6.5:** comparar todos los campos y saldos del conteo histórico antes y después de una corrección posterior: deben permanecer idénticos.
- **D7.6:** intentar crear stock inicial con `occurredAt` anterior a su momento de registro se rechaza.
- **D7.7:** stock inicial registrado el 10 de septiembre a las 15:30 locales: `observation_started_at` es ese instante; P4 no usa períodos anteriores y el 11 es el primer día calendario completo elegible.

## Casos aún no definidos aquí

- P6: siguen pendientes la validación de fuentes, la vigencia máxima del precio y la comparabilidad de ubicación y condición de compra. Ninguno de estos pendientes impide avanzar con P1–P5.

## Integración 1 (v1.2, 06-10) — decisiones de implementación

Fuente: `Arquitectura.md` v1.2 (§4.2, §4.3, §5 y decisiones C1, C5, C11, C12 y C14).

- **Modelo congelado:** la migración `0003_procurement` crea todas las tablas de la §4.2 (`users`, `suppliers`, `supplier_products`, `scrape_runs`, `price_quotes`, `suggestions`, `orders`) y agrega `created_by` a `movements` y `counts`. No existe `suggestion_revisions` (C11) ni costo de despacho (C4). Los precios se guardan con IVA incluido (C14) y sólo vienen del scraping (C2).
- **Tiempo de entrega (C12):** el mayor `lead_time_days` entre los proveedores activos que venden el insumo (`supplier_products` y `suppliers` activos), en días corridos. Sin proveedores, cuenta como 0 días y la regla equivale a la P5 original.
- **Alerta (C1):** `stock proyectado a la llegada = max(0, stock − consumo diario × L)`; hay alerta si es `≤` el stock seguro. Sin consumo calculable, se compara el stock actual (como antes).
- **Órdenes en tránsito:** suma de `suggestions.purchased_qty` de las órdenes en estado `APPROVED`. Si hay alerta y hay algo en tránsito, `alert_status = IN_TRANSIT` (alerta atendida). `GET /alerts` lista sólo las `LOW_STOCK`, que son las que requieren pedir.
- **Cantidad sugerida (C5):** `max(0, 2 × consumo semanal − stock proyectado a la llegada − en tránsito)`. El redondeo a formatos completos es de la sugerencia (P7), no del panel.
- **`order_by_at`:** momento más tardío para pedir, `ahora + ((stock − stock seguro) / consumo diario − L)` días. Puede quedar en el pasado: significa que el pedido está atrasado.
- **`balance_after`:** saldo del insumo inmediatamente después de cada movimiento, sumando todos sus movimientos (incluidas reversas) en orden de ocurrencia `(occurred_at, created_at, id)`. El último siempre coincide con el stock actual. El historial se sigue entregando en orden de `id`.
- **`created_by`:** `null` hasta que exista la autenticación (REQ-14); después, `{id, name}` del usuario.
- **`PATCH /items/{id}`:** cambia sólo los campos enviados (`name`, `manual_safe_stock`; `null` en este último vuelve al umbral automático). La unidad base no se puede cambiar. Un cuerpo vacío responde `422 EMPTY_UPDATE`.

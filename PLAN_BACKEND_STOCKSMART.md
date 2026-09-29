# Plan del backend de StockSmart

**Estado:** decisiones de backend consolidadas; las validaciones pendientes se señalan expresamente.

**Fecha de revisión:** 28 de septiembre de 2026.

**Uso de este archivo:** las respuestas P1–P12 son la base funcional del MVP. El estado de implementación que sigue describe el backend de este repositorio; no cambia los requisitos ni las decisiones funcionales del plan.

## Estado actual de implementación

**Corte: 28 de septiembre de 2026.** El Goal 1 implementa el núcleo backend de **P1–P5 y D1–D7**. El repositorio contiene solo el backend; no incluye una interfaz de usuario.

| Requisito | Estado en este repositorio |
|---|---|
| P1 | Implementado: API y persistencia de insumos y stock inicial. El alcance de demostración del MVP sigue siendo un insumo crítico. |
| P2 | Implementado: entradas, salidas, unidades base, stock inicial, reversiones y correcciones trazables; movimientos inmutables. |
| P3 | Implementado para conteos, ajustes atómicos, faltante y tasa por período. La función matemática de reducción frente a una tasa base está probada; cargar y administrar la línea base corresponde a P9 y no está implementado. |
| P4 | Implementado: consumo de salidas, días completos locales, historia insuficiente/preliminar, proyección y agotamiento fuera de rango. |
| P5 | Implementado: umbral automático dinámico, override manual, alertas y cantidad base a reponer. No calcula formatos comerciales. |
| D1–D7 | Implementados: inmutabilidad, reversas, observación confiable, historial retroactivo, conciliaciones D6 y orden temporal determinista. |
| P6 | Pendiente: validar fuentes y condiciones de uso. No hay adaptadores ni consultas reales a proveedores. |
| P7 | Pendiente: formatos comerciales, comparación de ofertas, proveedor recomendado y ahorro potencial por orden. |
| P8 | Pendiente: autenticación, perfiles/permisos, flujo de decisión y órdenes internas. |
| P9 | Parcialmente cubierto por los cálculos P3; pendientes la línea base, métricas completas del piloto y su evaluación comparativa. |
| P10 | Pendiente: mecanismo de respaldo automático, retención y restauración comprobada. |
| P11 | Parcial: OpenAPI documenta el contrato REST del núcleo P1–P5; falta el contrato e implementación de las operaciones P6–P10 y su coordinación con el equipo de frontend. |
| P12 | Parcial: el núcleo usa Python, FastAPI, PostgreSQL, SQLAlchemy y Alembic. Despliegue, autenticación y servicios operacionales siguen pendientes de decisión e implementación. |

El alcance actual no declara cumplidas metas del piloto ni reemplaza las validaciones pendientes del plan. La API y las pruebas del núcleo se describen en el [README](README.md).

## 1. Resumen del proyecto

StockSmart busca apoyar la reposición de insumos de una pyme gastronómica. Los documentos describen inventarios y compras gestionados con registros dispersos y decisiones reactivas, que pueden causar quiebres de stock, pérdidas de insumos y compras sin comparación de precios. El backend previsto registraría inventario, estimaría cuándo reponer, consultaría precios, sugeriría una compra y dejaría la decisión final a una persona.

Este plan se limita al backend. Las referencias a quienes usan el sistema sirven para definir permisos y operaciones; no aborda acuerdos comerciales, patrocinio ni firmas.

**Fuentes principales:** [caso de negocio de Entrega 1](<../Proyecto GPTI/Entrega 1/1.1.1 Caso de Negocio.pptx>), [acta del proyecto](<../Proyecto GPTI/Entrega 1/1.2.1 Acta de Constitucion de Proyecto.docx>), [reporte de avance](<../Proyecto GPTI/Entrega 1/1.5 Reporte de Avance de Proyecto en Primera Entrega.pptx>), [preguntas y discrepancias registradas](../../docs/decisions-and-questions.md), [rúbrica de Entrega 2](<../../Rubricas Entrega-2.pdf>).

## 2. Lo que ya está definido

### Indicado explícitamente en los documentos

- El producto propuesto es una aplicación web de apoyo a inventario y reposición.
- El backend debe permitir registrar stock inicial, entradas, salidas y conteos físicos de insumos.
- Debe calcular consumo promedio semanal y proyectar la fecha de agotamiento o quiebre.
- Debe alertar cuando corresponda reponer según un umbral de stock seguro.
- Debe obtener y comparar precios de al menos dos proveedores mediante consulta automatizada de sitios públicos, siempre que esas fuentes resulten viables.
- Debe sugerir una orden con cantidad y proveedor. Una persona debe aprobarla explícitamente antes de emitirla; la compra automática queda fuera de alcance.
- Debe calcular una medida asociada a conteos físicos y alimentar un panel de métricas.
- La demostración mínima descrita en el acta cubre el flujo completo para al menos un insumo crítico. Otras secciones mencionan varios insumos críticos.
- Las metas propuestas son reducir quiebres de stock un 30 %, «merma» un 20 % y lograr un 5 % de ahorro frente a una referencia. Son objetivos por validar, no resultados obtenidos.
- El curso exige un MVP funcional y demostrable. No exige que opere en producción. Para la Entrega 2 pide requisitos, arquitectura, tecnologías, plan de pruebas y evidencia de implementación.

### Decisiones del MVP tomadas después de los documentos

- La demostración cubrirá un solo insumo crítico y un solo inventario; el modelo podrá admitir más insumos posteriormente.
- Los movimientos no se editarán ni borrarán: los errores se corregirán con reversas trazables. Los conteos ajustarán el stock teórico al físico.
- La «merma» del MVP será el faltante detectado por conteo; su meta se evaluará mediante una tasa comparable con la línea base, según P3.
- La recomendación de proveedor comparará el desembolso necesario para comprar formatos comerciales completos, según P7.
- La aprobación será humana, la orden se registrará dentro de StockSmart y el ahorro se informará como potencial.
- Se exigirán respaldos automáticos cada 24 horas, siete copias conservadas fuera de la base operacional y una prueba de restauración antes de Entrega 3. El mecanismo técnico aún debe elegirse.

### Inferencias de diseño todavía por validar

- Conviene mantener separados los cálculos de inventario y reposición de la obtención de precios, para que el fallo de un proveedor no altere las reglas del inventario.
- El backend probablemente necesitará registros diferenciados de insumos, movimientos, conteos, precios consultados, sugerencias y decisiones humanas.
- Una sugerencia debería conservar los datos y reglas usados para generarla, de modo que pueda explicarse y probarse después.

Estas inferencias son propuestas de diseño; las decisiones detalladas y los pendientes se encuentran en la sección 3.

## 3. Decisiones para el backend

P1–P11 fijan el comportamiento previsto del MVP. El núcleo P1–P5 y D1–D7 está implementado según el estado anterior. P6 requiere verificar las fuentes de precios y P12 sigue parcialmente abierto. La fórmula de P3 tiene pruebas; los cálculos de P7 deberán probarse con ejemplos independientes antes de implementarlos.

### P1. Alcance mínimo del MVP

**Respuesta:** El MVP funcionará para **un único insumo crítico y un único inventario correspondiente al negocio piloto**. El insumo se parametrizará para que posteriormente puedan agregarse otros, pero la demostración de la Entrega 3 cubrirá el flujo completo para uno solo.

Esto coincide con el objetivo de demostrar el flujo completo para al menos un insumo crítico del [acta del proyecto](<../Proyecto GPTI/Entrega 1/1.2.1 Acta de Constitucion de Proyecto.docx>).

Como insumo de prueba provisional se propone **azúcar blanca granulada Iansa de 1 kg**, porque aparece en [Central Mayorista](https://www.centralmayorista.cl/p/azucar-1kg-iansa-577377) y [Alvi](https://fe-browse-alvi-gcp-alvi-prod.alvi.cl/ofertas/especial-almacenero). Esto no confirma que los precios sean accesibles automáticamente ni aplicables al piloto.

---

### P2. Unidades y movimientos

**Respuesta:** Cada insumo tendrá una **unidad base configurable** según corresponda (`kg`, `L` o `unidad`). Para el insumo del MVP se trabajará con una única unidad base y todos los movimientos serán convertidos a ella.

Existirán cuatro tipos de movimiento:

1. **Stock inicial**
2. **Entrada**, por recepción o compra.
3. **Salida**, por consumo o uso.
4. **Ajuste por conteo físico**.

No se permitirá que una salida deje el stock en valores negativos. Si no existe stock suficiente, el sistema rechazará el movimiento e informará al usuario.

Los movimientos realizados no se eliminarán ni modificarán directamente. Si existe un error, se registrará un **movimiento de reversa/corrección** asociado al movimiento original y posteriormente, si corresponde, el movimiento correcto. De esta manera se conserva trazabilidad.

El [acta del proyecto](<../Proyecto GPTI/Entrega 1/1.2.1 Acta de Constitucion de Proyecto.docx>) exige registrar stock inicial, entradas y salidas.

---

### P3. Conteos físicos y «merma»

**Respuesta:** En cada conteo se comparará el stock físico con el stock teórico inmediatamente anterior:

`diferencia = stock físico - stock teórico`

`faltante del conteo = max(stock teórico - stock físico, 0)`

Una diferencia positiva será un ajuste positivo y no sumará al faltante. Tras guardar el conteo y su ajuste asociado, el stock teórico quedará igual al físico para no volver a contabilizar la misma diferencia. El conteo y el ajuste se registrarán una sola vez como una operación, conservando ambos valores para auditoría.

En los entregables del proyecto este **faltante de inventario** se denomina «merma». La medida no permite afirmar por sí sola que hubo desperdicio: puede incluir un error de registro. Se podrá anotar una causa opcional (`vencimiento`, `deterioro`, `error de registro`, `desconocida` u `otra`), sin alterar el cálculo.

Para comparar períodos se calculará:

`cantidad disponible = stock al inicio del período + entradas del período + ajustes positivos que incorporen existencias efectivas`

`tasa de merma (%) = faltantes acumulados del período / cantidad disponible × 100`

Las reversas de movimientos erróneos no se contarán dos veces en el denominador. Si la cantidad disponible es cero o no puede reconstruirse con datos confiables, la tasa será **no calculable**.

La meta se evaluará con la misma fórmula en la línea base y en el piloto:

`reducción de merma (%) = (tasa de línea base - tasa del piloto) / tasa de línea base × 100`

La meta del 20 % se considerará alcanzada si la reducción es al menos 20 %. Se requieren datos históricos suficientes y períodos comparables. Si la línea base falta o su tasa es cero, se mostrará el faltante observado, pero la reducción porcentual y el cumplimiento de la meta quedarán como **no verificables**. Esta definición desarrolla el cálculo a partir de conteos establecido en el [acta](<../Proyecto GPTI/Entrega 1/1.2.1 Acta de Constitucion de Proyecto.docx>) y el [caso de negocio](<../Proyecto GPTI/Entrega 1/1.1.1 Caso de Negocio.pptx>).

---

### P4. Consumo y proyección

**Respuesta:** El consumo se calculará utilizando exclusivamente los **movimientos de salida**, excluyendo ajustes, conteos físicos y entradas.

El consumo promedio semanal se obtendrá preferentemente utilizando las últimas **4 semanas de información**:

`consumo semanal promedio = consumo últimos 28 días / 4`

Mientras existan menos de 28 días, se utilizará la historia disponible de días completos, normalizada a una semana. Con menos de **7 días de historia**, se mostrará **«estimación preliminar»**. Sin al menos un día completo de datos no se calculará un promedio; la ausencia de registros debe distinguirse de un consumo observado igual a cero.

A partir del consumo diario promedio:

`consumo diario = consumo semanal promedio / 7`

La fecha estimada de agotamiento será:

`días restantes = stock actual / consumo diario promedio`

`fecha de agotamiento = fecha actual + días restantes`

Si el consumo promedio es cero, no se calculará una fecha de agotamiento.

Esto desarrolla el requerimiento de calcular consumo promedio semanal y fecha estimada de quiebre del [acta](<../Proyecto GPTI/Entrega 1/1.2.1 Acta de Constitucion de Proyecto.docx>).

---

### P5. Alerta y cantidad sugerida

**Respuesta:** Para el MVP, el **stock seguro será configurable**, pero su valor inicial corresponderá a **una semana de consumo promedio**:

`stock seguro = consumo semanal promedio`

La alerta se activará cuando:

`stock actual ≤ stock seguro`

La alerta permanecerá abierta mientras `stock actual ≤ stock seguro`. Se reevaluará después de cualquier entrada, conteo, ajuste, corrección o cambio de umbral que afecte el resultado; aprobar una orden no aumenta el stock.

La cantidad sugerida buscará dejar inventario suficiente para **dos semanas de consumo**:

`stock objetivo = 2 × consumo semanal promedio`

`cantidad a reponer = max(0, stock objetivo - stock actual)`

Después se ajustará al formato comercial del proveedor. Por ejemplo, si deben comprarse 13 kg y el proveedor vende cajas equivalentes a 10 kg:

`cantidad de cajas = ceil(13 / 10) = 2 cajas`

El sistema deberá mostrar tanto las unidades comerciales a comprar como la cantidad equivalente en la unidad base.

---

### P6. Fuentes de precios

**Respuesta:** Para el MVP se utilizarán como proveedores iniciales **Central Mayorista y Alvi**, sujetos a validación técnica y de condiciones de uso antes de finalizar E2.

Ambos sitios muestran nombre, presentación y precio del insumo de prueba. Central Mayorista muestra una manga de diez unidades; Alvi muestra precios diferenciados según cantidad. [Central Mayorista](https://www.centralmayorista.cl/p/azucar-1kg-iansa-577377), [Alvi](https://fe-browse-alvi-gcp-alvi-prod.alvi.cl/ofertas/especial-almacenero). Los precios, disponibilidad y condiciones pueden cambiar.

**No se asumirá que la existencia pública de esos datos implica autorización para scraping.** Antes de integrarlos deberán revisarse las condiciones de uso y la factibilidad técnica de cada fuente. El [acta](<../Proyecto GPTI/Entrega 1/1.2.1 Acta de Constitucion de Proyecto.docx>) reconoce el riesgo de bloqueo o restricciones y exige usar datos públicamente accesibles respetando las condiciones aplicables.

Cada consulta guardará:

`proveedor, producto, formato, cantidad mínima, precio, disponibilidad y fecha/hora de consulta`.

Si un proveedor falla, se mostrará el precio disponible del otro con una advertencia. No se calculará ahorro ni se recomendará un proveedor como «más conveniente» sin **dos precios válidos y vigentes**. El último precio exitoso podrá mostrarse como histórico, marcado como desactualizado y excluido de la comparación. **Pendiente de validar:** antigüedad máxima de un precio vigente y si ambos precios aplican a la misma ubicación y condición de compra.

---

### P7. Comparación y ahorro

**Respuesta:** Cada oferta guardará por separado `precio_formato`, `cantidad_formato`, `unidad_base`, cantidad mínima de formatos, tramo de precio aplicable y fecha de consulta. También se calculará un precio por unidad base para mostrarlo, pero **la recomendación utilizará el costo total de la compra factible**.

Para una necesidad de reposición `Q`, se evaluará cada formato comercial disponible:

`formatos necesarios = max(ceil(Q / cantidad_formato), mínimo de formatos exigido)`

`cantidad comprada = formatos necesarios × cantidad_formato`

`costo de compra = formatos necesarios × precio_formato aplicable a esa cantidad`

Si un proveedor tiene varios formatos o tramos de precio, se compararán sus opciones factibles y se conservará la de menor costo total. La sugerencia mostrará cantidad requerida, cantidad comprada, excedente que ingresará al inventario y desembolso. Por ejemplo, para reponer 13 kg: 13 bolsas de 1 kg a CLP 1.200 cuestan CLP 15.600; dos cajas de 10 kg a CLP 10.000 cuestan CLP 20.000 y dejan 7 kg adicionales.

En el MVP solo se compararán ofertas del mismo insumo considerado equivalente, preferentemente la misma marca y presentación base. Si no se puede establecer equivalencia o disponibilidad, no se recomendará automáticamente un proveedor. Los precios deben corresponder a la misma modalidad de compra e incluir impuestos cuando formen parte del precio publicado. El despacho quedará fuera del MVP salvo que pueda determinarse de forma comparable para ambos proveedores.

El proveedor habitual del piloto será la referencia acordada. Para la **misma necesidad `Q`**, se calculará la compra factible y el costo total en ese proveedor y en el sugerido:

`ahorro potencial (%) = (costo de referencia - costo sugerido) / costo de referencia × 100`

Si el costo de referencia no existe, no es válido o es cero, el ahorro será **no calculable**. Un resultado negativo se mostrará como sobrecosto potencial. Este indicador es **estimado**, porque el MVP registra una orden interna aprobada pero no verifica la compra efectiva. El [caso de negocio](<../Proyecto GPTI/Entrega 1/1.1.1 Caso de Negocio.pptx>) exige comparar la compra sugerida con una alternativa de referencia, sin definir todavía su construcción.

---

### P8. Decisión humana sobre la sugerencia

**Respuesta:** El MVP tendrá dos perfiles funcionales:

**Encargado de inventario/bodega:** registra stock inicial, entradas, salidas y conteos físicos; consulta inventario, alertas y precios.

**Dueño/administrador:** puede realizar las mismas operaciones y además revisar, corregir, aprobar o rechazar sugerencias de compra.

StockSmart calculará la cantidad a reponer y, cuando haya dos ofertas comparables y vigentes, sugerirá un proveedor. Antes de aprobar, el dueño/administrador podrá modificar la cantidad o elegir otro proveedor; el costo se recalculará y ambas versiones quedarán en el historial.

En el MVP, **“emitir una orden” significará registrar formalmente una orden aprobada dentro de StockSmart y generar su resumen/documento**. No se realizará automáticamente la compra ni se enviará la orden directamente al proveedor. Esa integración queda fuera del alcance inicial.

Se conservará historial de:

- sugerencia original;
- cantidades;
- precios consultados;
- proveedor sugerido;
- fecha y hora de los precios;
- usuario que tomó la decisión;
- modificaciones realizadas;
- aprobación o rechazo;
- fecha y hora de la decisión;
- motivo opcional del rechazo.

Esto mantiene la decisión humana exigida por el [caso de negocio](<../Proyecto GPTI/Entrega 1/1.1.1 Caso de Negocio.pptx>) y el [acta](<../Proyecto GPTI/Entrega 1/1.2.1 Acta de Constitucion de Proyecto.docx>).

---

### P9. Métricas y datos de validación

**Respuesta:** Se utilizarán las siguientes definiciones:

**Quiebre de stock:** evento en que el stock del insumo llega a cero. Solo se contará un nuevo evento después de que exista una reposición y posteriormente vuelva a cero, según el [caso de negocio](<../Proyecto GPTI/Entrega 1/1.1.1 Caso de Negocio.pptx>).

**Merma operativa:** faltante detectado en conteos físicos, expresado como cantidad y como tasa sobre la cantidad disponible del período, según P3:

`merma = max(stock teórico - stock físico, 0)`

**Ahorro potencial:** diferencia entre el costo factible de cubrir la misma necesidad con el proveedor habitual y con el proveedor sugerido, según P7. No se presentará como ahorro realizado porque StockSmart no confirma la compra externa.

`ahorro % = (referencia - sugerido) / referencia × 100`

La línea base deberá provenir del negocio piloto y usar la misma fórmula y un período comparable al del piloto. Si no hay datos históricos suficientes, la línea base es cero o no existe una comparación válida, se mostrará **«línea base insuficiente»** o **«meta no verificable»**, sin declarar su cumplimiento.

El backend deberá entregar, aunque todavía no exista información suficiente:

`stock actual`, `consumo semanal`, `fecha proyectada de quiebre`, `alertas`, `cantidad de quiebres`, `faltante acumulado`, `tasa de merma`, `órdenes sugeridas`, `órdenes aprobadas/rechazadas`, `ahorro potencial por orden`, `ahorro potencial promedio` y un indicador de `datos suficientes/insuficientes`.

Las metas propuestas siguen siendo ≥30 % de reducción de quiebres, ≥20 % de reducción de merma y ≥5 % de ahorro promedio por orden, sujetas a medición válida según el [caso de negocio](<../Proyecto GPTI/Entrega 1/1.1.1 Caso de Negocio.pptx>).

---

### P10. Datos, acceso y respaldo

**Respuesta:** El MVP almacenará en PostgreSQL centralizado en la nube solo los datos necesarios: identidad y rol de usuario, insumo, movimientos, conteos, precios consultados, alertas, sugerencias, órdenes, decisiones y datos de métricas. No guardará datos bancarios, tarjetas ni información de pago. Los perfiles serán **encargado de inventario** y **administrador**, con los permisos de P8.

Se ejecutará automáticamente un respaldo al menos cada 24 horas. Cada respaldo incluirá el esquema y los datos necesarios para reconstruir esos registros y, si la autenticación se almacena en un esquema o servicio administrado, también sus usuarios y roles o un procedimiento comprobado para recuperarlos. Los respaldos se guardarán en una ubicación privada distinta de la base operacional y se conservarán al menos las últimas siete copias diarias. No se guardarán datos operacionales ni respaldos en GitHub.

Un mecanismo posible es una tarea programada que genere un volcado de PostgreSQL y lo envíe a almacenamiento privado; **el proveedor, la tarea y el almacenamiento aún deben elegirse y probarse**. Antes de la Entrega 3 se restaurará al menos una copia en un entorno separado y se comprobará que los registros y accesos necesarios se reconstruyen. No se considerará cumplido el requisito solo por programar la copia: deben existir evidencia de ejecución y una restauración exitosa.

Si se elige Supabase gratuito, no se deben atribuir a ese plan respaldos automáticos incluidos. La solución propia o un plan que los incluya debe cubrir este requisito. [Precios de Supabase](https://supabase.com/pricing), [guía de copia y restauración](https://supabase.com/docs/guides/platform/migrating-within-supabase/backup-restore).

---

### P11. Contrato con la página

**Respuesta:** El backend expondrá una API REST en JSON que permita al frontend realizar al menos las siguientes operaciones:

`POST /items` — crear/configurar el insumo.

`GET /items/{id}` — consultar el estado actual del inventario.

`POST /movements` — registrar entradas, salidas y correcciones.

`POST /counts` — registrar un conteo físico y calcular diferencias/merma.

`GET /alerts` — consultar alertas activas.

`GET /prices/{itemId}` — consultar precios previamente obtenidos; la actualización desde proveedores tendrá una operación separada.

`POST /prices/{itemId}/refresh` — solicitar una actualización controlada de precios a los proveedores validados.

`GET /suggestions/{id}` — obtener una sugerencia ya guardada.

`POST /suggestions` — generar una nueva sugerencia a partir de datos y precios válidos.

`PATCH /suggestions/{id}` — modificar una sugerencia pendiente, con recálculo e historial.

`POST /suggestions/{id}/decision` — aprobar o rechazar una sugerencia pendiente.

`GET /orders` — consultar órdenes e historial.

`GET /metrics` — consultar métricas del piloto.

No aparece en los documentos entregados un diseño de interfaz previamente impuesto. El contrato API anterior es una propuesta inicial que se precisará con el frontend. Un flujo de interfaz simple sería:

**Inventario → alerta → precios → sugerencia → aprobación → orden**

más secciones secundarias de movimientos/conteos y métricas.

---

### P12. Tecnologías y ejecución



**Respuesta provisional:** El equipo considera el siguiente stack. Es razonable para el MVP, pero todavía depende de la experiencia del equipo, la prueba de proveedores y la elección del mecanismo de respaldo:

**Frontend:** React + TypeScript.

**Backend:** Python + FastAPI.

**Base de datos y autenticación:** PostgreSQL mediante Supabase.

**Web Scraping:** `requests/BeautifulSoup` cuando el sitio lo permita técnicamente; Playwright solo para sitios que requieran renderizado dinámico.

**Despliegue:** frontend en Vercel y backend en Render/Railway o servicio equivalente, utilizando planes gratuitos o estudiantiles durante el MVP.

**Repositorio:** GitHub.

La aplicación deberá poder ejecutarse íntegramente desde un navegador web sin instalaciones locales para el usuario final.

La elección definitiva podrá modificarse si el equipo tiene mayor experiencia con otro stack, pero manteniendo la API separada del scraping mediante adaptadores por proveedor. Esto permitirá reemplazar un proveedor sin modificar la lógica principal del inventario.


## 4. Posibles inconsistencias

- **«Merma» frente a desperdicio:** se resolvió la fórmula operativa de P3 como faltante de inventario detectado por conteo. Sigue siendo necesario describirla así en la demo: la causa del faltante puede ser un error de registro y no un desperdicio físico.
- **Uno o varios insumos:** para el MVP se resolvió en P1 demostrar el flujo completo con uno. Las menciones generales a varios insumos no amplían esa obligación de demostración.
- **Metas frente a aceptación técnica:** aprobar pruebas del backend no demuestra automáticamente reducciones del 30 %, 20 % o 5 %. La línea base y el período comparable son requisitos de medición separados.
- **Dos proveedores:** la comparación automática sigue condicionada a comprobar acceso, vigencia, equivalencia, ubicación y condiciones de compra de ambas fuentes. Esta validación de P6 aún está abierta.
- **Respaldos:** P10 fija cobertura, frecuencia y prueba de restauración, pero falta seleccionar el servicio y comprobar que el mecanismo realmente cubre los datos de autenticación y aplicación.
- **Cifras de versiones anteriores:** los escenarios financieros del caso de negocio cambian entre versiones. No se usarán como datos iniciales ni como fórmulas del producto.

## 5. Producto esperado

Según las decisiones P1–P11, el backend final debería sostener estos flujos para un insumo crítico y un inventario:

1. Registrar un insumo, su stock inicial, sus movimientos y un conteo físico.
2. Consultar existencias, consumo calculado, proyección de agotamiento y alerta de reposición.
3. Obtener precios vigentes y comparables de dos proveedores, calcular el costo de los formatos completos necesarios y guardar la información usada.
4. Generar una sugerencia con cantidad, proveedor y costo total explicables, pendiente de decisión humana.
5. Permitir corrección, aprobación o rechazo con historial. La aprobación genera una orden interna; no ejecuta una compra automática.
6. Consultar faltantes por conteo, tasa de merma, ahorro potencial y estado de suficiencia de los datos.

P4 y P6 indican cómo mostrar historia insuficiente, precios incompletos o una fuente caída. La antigüedad máxima aceptable de un precio sigue pendiente de validación.

## 6. Plan de desarrollo

| Fase | Objetivo y tareas principales | Entregable esperado | Dependencias |
|---|---|---|---|
| 1. Definir requisitos | Convertir P1–P11 en requisitos y ejemplos numéricos verificables; separar reglas del backend de metas del piloto. | Requisitos, reglas de negocio y criterios de aceptación. | Decisiones de la sección 3. |
| 2. Validar fuentes | Revisar dos sitios, sus condiciones de consulta y la comparabilidad de precios; definir el caso de fallo. | Fuentes viables o decisión explícita de ajustar el alcance. | P6 y P7. |
| 3. Diseñar solución | Confirmar P12; definir datos, estados, permisos, respaldo y contrato con la página. | Modelo de datos, contrato del backend y diagrama simple. | Fases 1 y 2; P10–P12. |
| 4. Crear prototipo vertical | Construir para un insumo el flujo registro → cálculo → alerta → sugerencia → decisión; usar datos de prueba rotulados mientras se integra una fuente válida. | Primer flujo ejecutable y evidencia para Entrega 2. | Fase 3. |
| 5. Completar integraciones y métricas | Integrar las fuentes validadas, manejar fallos, agregar conteos y métricas acordadas. | Backend del alcance comprometido. | Fases 2 y 4. |
| 6. Probar y corregir | Probar cálculos con ejemplos independientes, equivalencia de precios, errores de datos y aprobación obligatoria; vincular cada prueba con un requisito. | Plan y resultados de pruebas; defectos críticos resueltos. | Fases 4 y 5. |
| 7. Preparar entrega | Repetir el flujo completo, documentar ejecución y límites, y preparar evidencia de métricas que se pueda sostener con los datos disponibles. | Demo funcional y documentación coherente con lo implementado. | Fase 6. |

El [calendario del curso](../../docs/course-guide.md) indica Entrega 2 el 20 de octubre de 2026, corte de métricas el 10 de noviembre y Entrega 3 el 24 de noviembre. Si la observación disponible no permite verificar una meta, debe informarse ese límite sin presentarla como resultado alcanzado.

## 7. Próximos pasos

- [x] Definir P1–P5 y las precisiones D1–D7 para el núcleo de inventario.
- [x] Implementar el flujo backend de inventario P1–P5: stock, movimientos, conteos, consumo, proyección y alertas.
- [x] Documentar y probar el contrato API, las reglas principales y las migraciones del núcleo.
- [ ] Probar las dos fuentes de precios y fijar antigüedad máxima, ubicación y condición de compra comparables.
- [ ] Implementar formatos comerciales, comparación de ofertas y ahorro potencial de P7 después de validar P6.
- [ ] Implementar autenticación, roles, decisiones humanas y órdenes internas de P8.
- [ ] Implementar gestión de línea base y métricas comparativas completas de P9.
- [ ] Elegir e implementar el mecanismo de respaldo de P10; comprobar la restauración.
- [ ] Completar el contrato de P11 para precios, sugerencias, decisiones, órdenes y métricas; coordinar los puntos de integración con frontend.
- [ ] Confirmar despliegue y servicios operacionales de P12.
- [ ] Preparar la demostración completa y la evidencia de métricas solo cuando existan datos comparables válidos.

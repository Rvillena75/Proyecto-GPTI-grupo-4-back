# Importación de movimientos CSV

Implementación local de DRS v1.4 (10/11/2026), REQ-18 y RN-19, y Plan de Pruebas v2 PR-18, PR-19 y PR-20. No realiza exportación ni modifica el catálogo. Ambos roles autenticados pueden importar, igual que al registrar movimientos manuales.

`POST /movements/import-csv`, Bearer JWT, cuerpo original del archivo con `Content-Type: text/csv`. No utiliza multipart. UTF-8 con BOM opcional; delimitador coma o punto y coma. Máximo 1 MiB y 1000 filas de datos, seis columnas exactas:

```csv
insumo,cantidad,unidad,tipo,fecha,observacion
1,4,kg,ENTRADA,2026-11-11T12:00:00-03:00,Recepción
1,2,kg,SALIDA,2026-11-11T12:01:00-03:00,Consumo
1,500,g,SALIDA,2026-11-11T12:02:00-03:00,Café
```

Sustituir ID y fechas por valores existentes y pasados del entorno. `insumo` es el ID mostrado en la interfaz; `cantidad` es positiva, sin miles, con punto o coma decimal (entre comillas al usar coma como delimitador); unidad compatible `kg/g`, `L/mL` o `unidad`; tipo `ENTRADA/SALIDA`. `fecha` acepta ISO con zona o AAAA-MM-DD, interpretada al inicio del día en America/Santiago. No se admiten fechas futuras o anteriores a la observación inicial. `observacion` es opcional en valor, la columna es obligatoria y admite hasta 2000 caracteres; comillas CSV permiten comas y saltos de línea.

Estas convenciones y límites son decisiones técnicas de implementación porque las fuentes no fijan un formato exacto. La primera fila de datos es fila 1, independiente del encabezado y de saltos de línea dentro de comillas. Fila 0 denota un error de archivo/encabezado. Filas vacías se rechazan.

Respuesta correcta: `{"imported":3,"movement_ids":[2,3,4],"replayed":false}`. Error de validación: HTTP 422, `error.code=CSV_REJECTED`, `error.message` y `error.details=[{"row":2,"code":"ITEM_NOT_FOUND","message":"..."}]`. Exceso de bytes: 413; tipo de contenido incorrecto: 415; sin sesión: 401. Los errores de negocio se localizan en la interfaz. Cada respuesta informa cero persistencias en caso de rechazo, aunque las secuencias PostgreSQL puedan avanzar al descartar transacciones.

Una transacción externa contiene todas las filas. Se bloquean insumos ordenados por ID para reducir interbloqueos entre cargas. Cada fila usa un savepoint y el mismo servicio que el registro manual; las filas válidas anteriores aportan stock acumulado, las inválidas no. Se recogen todos los errores recuperables y se revierte el archivo completo. Los movimientos conservan usuario, fecha y las reglas del historial inmutable, incluidos conteos conciliados. Un CSV desordenado temporalmente sigue las reglas históricas existentes: puede ser rechazado aunque el saldo final sea positivo.

Duplicados: una fila idéntica normalizada dentro del archivo se rechaza; una carga exitosa repetida devuelve el resultado original sin nuevos movimientos, incluso en concurrencia y con cambio de BOM/delimitador. La tabla `csv_imports` conserva digest SHA-256, IDs y autor, sin almacenar el archivo. No intenta deducir si un movimiento parecido previamente ingresado manualmente es un duplicado. Para dos movimientos deliberadamente idénticos, distinguir fecha/observación. No existe una opción que fuerce repetir el mismo lote. No hay exportación de valores CSV ni superficie nueva de fórmulas de planilla.

Migración **aditiva** `0007_csv_imports` sobre `0006_shipping_cost`. Ejecutada sólo en bases locales con sufijo `_test`; aplicar en remoto requiere aprobación y respaldo. El downgrade elimina el registro de reintentos, no se recomienda en operación.

Validación: `backend/tests/api/test_csv_imports.py` cubre éxito 5→6,5 kg, rollback, múltiples errores, UTF-8/BOM, comillas y saltos de línea, unidades, decimales chilenos, vacíos, columnas/tipos/fechas inválidos, duplicados, límites, roles/JWT, varios insumos y reintentos concurrentes. Chromium conectó frontend React → API FastAPI → PostgreSQL local, importó tres movimientos, repitió sin duplicar y rechazó dos errores preservando 6,5 kg. A 360 px no hubo desborde horizontal. No es una validación de producción ni de todos los navegadores exigidos por RNF-01.

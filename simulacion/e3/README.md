# Entorno local de datos para la simulación E3

Este directorio contiene **datos sintéticos**, una copia verificable del Dataset v2 congelado, costos logísticos supuestos y herramientas para cargar y comprobar el entorno en una base PostgreSQL aislada. No contiene credenciales ni datos productivos.

El equipo adoptó este montaje local como entorno de datos de la simulación E3. El montaje reproduce el inventario y verifica el costo de despacho; **todavía no implementa la comparación completa de políticas E3 ni acredita las metas 30/20/5**. Consultar [HALLAZGOS.md](HALLAZGOS.md) antes de interpretar resultados.

## Contenido

- `dataset_v2/`: copia sintética congelada con su manifiesto de hashes; no editar sus datos originales.
- `parametros_productos.json`: configuración separada de proveedores para E3, con despachos por pedido.
- `entorno_simulacion.py`: crea una base nueva local y la carga; también comprueba una base ya creada.
- `evidencia/verificacion_local_2026-11-15.json`: evidencia de una ejecución local previa, con precios históricos; no demuestra ejecución en otro computador.
- `resultados_locales/`: salidas generadas en cada computador; se excluyen de Git.

## Requisitos

Python 3.11 o superior, las dependencias ya definidas en `backend/pyproject.toml`, y PostgreSQL local funcionando con una cuenta que pueda crear una base. No cargar la base operativa ni exponer contraseñas en el repositorio. Si se requiere contraseña, configurarla de forma segura en el entorno local y no publicarla ni pegarla en conversaciones.

Desde la raíz de este repositorio, activar el entorno Python que tenga las dependencias del backend. Definir la URL de una **base nueva** terminada en `_test`. Se acepta exclusivamente PostgreSQL en `127.0.0.1`, `localhost` o `::1`; el usuario, puerto y autenticación dependen de la instalación local. Ejemplo sin contraseña para una instancia local configurada para permitirlo:

```powershell
$env:STOCKSMART_SIMULATION_DATABASE_URL = 'postgresql+psycopg://TU_USUARIO@127.0.0.1:55439/stocksmart_simulacion_e3_test'
python simulacion/e3/entorno_simulacion.py crear
```

El comando comprueba los hashes, rechaza una base preexistente y no borra ninguna; crea esa base local, aplica migraciones y reproduce el dataset. Inserta los tres proveedores, costos, productos y observaciones de precios históricos, y ejecuta la verificación. Si interrumpe el proceso después de crear la base, revise el estado: `crear` no la reemplazará ni reparará automáticamente.

Para verificar la base ya creada, con la misma variable configurada:

```powershell
python simulacion/e3/entorno_simulacion.py verificar
```

La comprobación lee los costos de PostgreSQL, ejecuta la comparación del backend al 08/10/2026 y verifica subtotal + despacho una sola vez, así como diferencias frente a cero. Comprueba `GET /suppliers` y `GET /prices/{item}` mediante el cliente local de pruebas; autenticación y reloj se sustituyen para ese chequeo. No crea usuarios ni habilita el frontend para compartirlo.

**No ejecutar la suite de pytest apuntando a esta base**: los fixtures del proyecto recrean su esquema y borrarían los datos. Para pytest, configurar otra base dedicada, nunca esta. Los precios del 07/10/2026 se conservan con sus fechas reales y no son vigentes; la comparación de prueba usa un reloj histórico explícito.

## Despachos de simulación

| Proveedor | CLP por pedido | Naturaleza |
|---|---:|---|
| Central Mayorista | 3.990 | Costo logístico hipotético, no tarifa oficial de Central |
| Jumbo | 4.990 | Supuesto referido a tarifa publicada para comunas de la RM |
| Santa Isabel | 3.990 | Supuesto referido a sus términos de servicio |

Se requiere validar cobertura, mínimos de compra y logística para el caso real. No aplicar estos valores en producción ni modificarlos en Dataset v2: los valores para E3 viven en `parametros_productos.json`.

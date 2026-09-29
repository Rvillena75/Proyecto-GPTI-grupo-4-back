# Instrucciones permanentes: backend de StockSmart

## Alcance y fuentes

- Este repositorio corresponde exclusivamente al backend de StockSmart. No crear frontend, ni siquiera uno temporal, ni modificar archivos o repositorios del equipo de frontend.
- `PLAN_BACKEND_STOCKSMART.md` es la fuente de verdad funcional. `docs/decisions.md` registra decisiones de implementación que precisan el plan sin reemplazarlo.
- Aplicar P1–P11 sin reinterpretarlos silenciosamente. Registrar y consultar una contradicción o un caso no definido antes de convertirlo en comportamiento del sistema. No inventar decisiones que el plan marque como pendientes.
- La validación de las fuentes de P6 no bloquea el núcleo de inventario P1–P5. No crear adaptadores reales para Central Mayorista o Alvi antes de validar cada fuente y sus condiciones de uso.
- Preferir una arquitectura simple y mantenible, adecuada para un MVP universitario.

## Diseño y datos

- Exponer las funcionalidades mediante una API REST JSON definida y documentada. Mantener el backend desacoplado de la implementación del frontend: OpenAPI, documentación de endpoints y ejemplos de solicitudes/respuestas constituyen el contrato de integración. Documentar como pendientes las decisiones que dependan del equipo de frontend; no asumir pantallas concretas.
- Separar las reglas de negocio de FastAPI y de las integraciones externas cuando esto facilite probarlas. Desacoplar las integraciones de proveedores mediante adaptadores.
- Los movimientos históricos son inmutables: corregir con nuevos movimientos trazables, según P2 y D1. Ninguna operación, incluidas reversas y correcciones, puede dejar stock negativo.
- No incorporar secretos, datos operacionales ni respaldos al repositorio.

## Verificación

- Toda regla de negocio relevante debe tener tests automatizados. Probar el backend mediante tests unitarios, de integración y de API, según corresponda; no depender de una interfaz gráfica.
- Antes de dar una tarea por terminada, ejecutar los tests correspondientes y comunicar el resultado. Una funcionalidad backend puede estar terminada sin que exista una interfaz visual que la consuma.
- Las metas de reducción de quiebres, merma y ahorro son métricas del piloto, no criterios de aceptación técnica del software. No declarar una meta cumplida sin datos y comparación válidos según el plan.

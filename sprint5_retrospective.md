# Sprint 5 Retrospective — ERP Django
## Semanas W16–W18 · Espiral 6: Celery e Integraciones

**Fecha:** 19/11/2026
**Responsable:** Felipe Sánchez Vázquez

## ¿Qué funcionó bien? (Keep)
1. El campo `correo_enviado` en Pedido evitó correos duplicados sin un sistema externo.
2. `@override_settings(EMAIL_BACKEND=locmem)` hizo triviales los tests de correo: `mail.outbox` captura todo sin enviar nada.
3. `CELERY_TASK_ALWAYS_EAGER=True` permitió trabajar en Windows sin Docker ni Redis: las tareas se ejecutan al momento.

## ¿Qué mejorar? (Improve)
1. Documentar mejor el arranque de los procesos (runserver, Stripe CLI, worker, beat).
2. Revisar que el usuario que compra tenga correo: los correos se iban a otra dirección.
3. Hacer push al terminar cada semana: al cambiar de computadora, GitHub fue el respaldo que salvó el proyecto.

## Acción de mejora (Kaizen) para Sprint 6
> "En el Sprint 6 (Dashboard), pre-calcularé los KPIs con annotate desde el principio, sin iterar en Python."

## Velocidad del Sprint 5

| HU | Pts plan. | Pts ent. |
|---|---|---|
| HU-E6-01 Infraestructura Celery+Redis | 3 | 3 |
| HU-E6-02 Tareas sin bloquear HTTP | 2 | 2 |
| HU-E6-03 Correo confirmación post-pago | 5 | 5 |
| HU-E6-04 Alerta stock bajo | 3 | 3 |
| HU-E6-05 Reporte diario 8 AM | 3 | 3 |
| HU-E6-06 Monitorear con Flower | 2 | 2 |
| **Total** | **18** | **18** |

**Velocidad Sprint 5:** 18 puntos
**Velocidad acumulada (S0–S5):** 123 puntos

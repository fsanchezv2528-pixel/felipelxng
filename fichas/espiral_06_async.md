# Ficha de Sistematización — Espiral 6
## ERP Django · Espiral E6: Celery e Integraciones

| Campo | Contenido |
|---|---|
| **Número de espiral** | 6 |
| **Nombre del ciclo** | Celery e Integraciones |
| **Semanas** | W16 – W18 |
| **Fecha de inicio** | ___/___/_____ |
| **Fecha de cierre** | 19/11/2026 |
| **Responsable** | Felipe Sánchez Vázquez |
| **Asesor** | MC. Román Fernando López González |

## 1. Objetivo del ciclo
Implementar procesamiento asíncrono con Celery para: correos transaccionales de confirmación de pedido, alertas de stock bajo y reportes diarios automáticos vía Celery Beat.

## 2. Tareas realizadas

| # | Tarea | Estado | Semana |
|---|---|---|---|
| 1 | pip install celery redis flower | ✅ | W16 |
| 2 | core/celery.py + core/__init__.py | ✅ | W16 |
| 3 | CELERY_* en settings.py | ✅ | W16 |
| 4 | docker-compose: worker + flower | ✅ | W16 |
| 5 | ventas/tasks.py (2 tareas placeholder) | ✅ | W16 |
| 6 | pip install django-anymail | ✅ | W17 |
| 7 | Template confirmacion_pedido.html + .txt | ✅ | W17 |
| 8 | Template alerta_stock.html + .txt | ✅ | W17 |
| 9 | enviar_confirmacion_pedido: correo real + idempotencia | ✅ | W17 |
| 10 | verificar_stock_bajo: alerta al admin | ✅ | W17 |
| 11 | Template reporte_ventas.html + .txt | ✅ | W18 |
| 12 | reporte_ventas_diario con annotate + Sum(F()) | ✅ | W18 |
| 13 | CELERY_BEAT_SCHEDULE (crontab + timedelta) | ✅ | W18 |
| 14 | docker-compose: celery_beat | ✅ | W18 |

## 3. Evidencias
- Tests: Ran 183 tests → OK
- Correo de confirmación: enviado tras el pago (idempotente con `correo_enviado`)
- Webhook local probado con Stripe CLI (`stripe listen`): pedido pasa a "pagado" y baja el stock
- Alerta de stock: enviada cuando stock < umbral
- Reporte: programado con `crontab(hour=8, minute=0)`; probado con `reporte_ventas_diario.apply().get()`
- Sitio en producción: https://felipelxng.onrender.com

## 4. Criterios de aceptación

| Criterio | Estado |
|---|---|
| enviar_confirmacion_pedido envía el correo | ✅ |
| correo_enviado=True previene duplicados | ✅ |
| Alerta de stock enviada al admin | ✅ |
| reporte_ventas_diario en BEAT_SCHEDULE | ✅ |
| verificar_stock_bajo en BEAT_SCHEDULE | ✅ |
| 183 tests OK | ✅ |

## 5. Problemas encontrados
1. Windows sin Docker ni Redis: se usó `CELERY_TASK_ALWAYS_EAGER=True` para ejecutar las tareas sin worker.
2. Los correos no llegaban: el usuario que compraba tenía otro correo registrado; se corrigió en /admin/.
3. En local los pedidos quedaban "pendientes": Stripe no puede llamar a 127.0.0.1; se resolvió con Stripe CLI.
4. Cambio de computadora: el proyecto se recuperó con `git clone` desde GitHub y el `.env` desde la USB.

## 6. Lecciones aprendidas
1. Las tareas asíncronas deben ser idempotentes porque pueden ejecutarse más de una vez.
2. En desarrollo conviene el backend de consola o locmem; el envío real solo en producción.
3. Subir a GitHub con frecuencia es el mejor respaldo.

## 7. Tiempo total invertido

| Categoría | Horas |
|---|---|
| Diseño | |
| Implementación | |
| Pruebas | |
| Documentación | |
| **Total Espiral 6** | |

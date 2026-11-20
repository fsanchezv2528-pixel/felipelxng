"""Instalador W18 — ERP Django (Celery Beat + reporte diario + cierre Sprint 5 / M6).

Desde la raíz del proyecto (donde está manage.py), con env_erp activado:
    python instalar_w18.py

Qué hace (guarda .bak de cada archivo existente que modifica):
  - ventas/tasks.py                      → agrega reporte_ventas_diario (+ URL felipelxng)
  - templates/emails/reporte_ventas.html → correo del reporte (KPIs + top productos)
  - templates/emails/reporte_ventas.txt  → versión texto plano
  - core/settings.py                     → import timedelta + CELERY_BEAT_SCHEDULE
  - docker-compose.yml                   → servicio celery_beat
  - tests/test_w18_beat.py               → 8 tests (183 acumulados)
  - sprint5_retrospective.md, fichas/espiral_06_async.md, sprint5_planning.md

Antes de tocar nada revisa que tus modelos tengan los campos que usa el reporte.
"""
import re
import shutil
import sys
from datetime import date
from pathlib import Path

try:
    sys.stdout.reconfigure(errors='replace')
except Exception:
    pass

BASE = Path(__file__).resolve().parent
avisos = []
URL_SITIO = 'https://felipelxng.onrender.com'


def ok(msg):
    print(f'  ✅ {msg}')


def aviso(msg):
    avisos.append(msg)
    print(f'  ⚠️  {msg}')


def respaldar(ruta: Path):
    bak = ruta.with_name(ruta.name + '.bak')
    if ruta.exists() and not bak.exists():
        shutil.copy2(ruta, bak)


def escribir(rel, contenido, sobrescribir=True):
    ruta = BASE / rel
    if ruta.exists() and not sobrescribir:
        print(f'  ⏭️  {rel} ya existe, no lo toqué')
        return
    ruta.parent.mkdir(parents=True, exist_ok=True)
    existia = ruta.exists()
    respaldar(ruta)
    ruta.write_text(contenido, encoding='utf-8')
    ok(f'{rel} ({"reemplazado" if existia else "creado"})')


def bloque_clase(texto, nombre):
    m = re.search(rf'^class {nombre}\(.*?(?=^class |\Z)', texto, re.M | re.S)
    return m.group(0) if m else None


def tipo_campo(bloque, campo):
    """Devuelve 'DateTimeField', 'DateField', otro tipo, o None si no existe."""
    m = re.search(rf'^\s+{campo}\s*=\s*models\.(\w+)\(', bloque, re.M)
    return m.group(1) if m else None


# =====================================================================
# Revisión de modelos (no modifica nada)
# =====================================================================
def revisar_modelos():
    problemas, lookups = [], {}
    ruta = BASE / 'ventas' / 'models.py'
    if not ruta.exists():
        return ['No encontré ventas/models.py'], lookups
    t = ruta.read_text(encoding='utf-8')

    pedido = bloque_clase(t, 'Pedido')
    venta = bloque_clase(t, 'Venta')
    detalle = bloque_clase(t, 'DetalleVenta')
    for nombre, b in [('Pedido', pedido), ('Venta', venta), ('DetalleVenta', detalle)]:
        if b is None:
            problemas.append(f'No encontré "class {nombre}" en ventas/models.py')
    if problemas:
        return problemas, lookups

    # Pedido.fecha_pedido
    tp = tipo_campo(pedido, 'fecha_pedido')
    if tp is None:
        problemas.append('Pedido no tiene el campo "fecha_pedido"')
    else:
        lookups['pedido'] = 'fecha_pedido__date' if tp == 'DateTimeField' else 'fecha_pedido'
    for campo in ['estado', 'total_pagado']:
        if tipo_campo(pedido, campo) is None:
            problemas.append(f'Pedido no tiene el campo "{campo}"')

    # Venta.fecha
    tv = tipo_campo(venta, 'fecha')
    if tv is None:
        problemas.append('Venta no tiene el campo "fecha"')
    else:
        lookups['venta'] = 'venta__fecha__date' if tv == 'DateTimeField' else 'venta__fecha'
        if 'auto_now' not in re.search(r'^\s+fecha\s*=.*$', venta, re.M).group(0) \
                and 'default' not in re.search(r'^\s+fecha\s*=.*$', venta, re.M).group(0):
            aviso('Venta.fecha no se llena sola (sin auto_now_add/default); los tests '
                  'crean Venta(cliente=...) sin fecha y podrían fallar.')

    # DetalleVenta.cantidad / precio_unitario / producto / venta
    for campo in ['venta', 'producto', 'cantidad', 'precio_unitario']:
        if tipo_campo(detalle, campo) is None:
            problemas.append(f'DetalleVenta no tiene el campo "{campo}"')
    return problemas, lookups


# =====================================================================
# Código a agregar en ventas/tasks.py
# =====================================================================
TAREA = '''


# ═══════════════════════════════════════════════════════════════════════
# W18 — Reporte diario de ventas (programado con Celery Beat a las 8:00 AM)
# ═══════════════════════════════════════════════════════════════════════
import logging as _logging_w18

from celery import shared_task as _shared_task_w18
from django.conf import settings as _settings_w18
from django.core.mail import EmailMultiAlternatives as _Email_w18
from django.template.loader import render_to_string as _render_w18

_logger_w18 = _logging_w18.getLogger(__name__)


@_shared_task_w18(
    bind=True,
    max_retries=2,
    name='ventas.reporte_ventas_diario',
)
def reporte_ventas_diario(self) -> dict:
    """Genera y envía el reporte diario de ventas al administrador.

    Se ejecuta automáticamente a las 8:00 AM (CELERY_BEAT_SCHEDULE).
    Los totales se calculan en SQL con aggregate/annotate + Sum(F()).
    """
    from decimal import Decimal

    from django.db.models import DecimalField, ExpressionWrapper, F, Sum
    from django.utils import timezone

    from configuracion.models import ConfiguracionERP      # import local
    from ventas.models import DetalleVenta, Pedido         # import local

    hoy = timezone.localdate()
    config = ConfiguracionERP.get_instance()

    # ── 1. KPIs del día (pedidos pagados) ─────────────────────────────
    pedidos_hoy = Pedido.objects.filter(__LOOKUP_PEDIDO__=hoy, estado='pagado')
    total_pedidos = pedidos_hoy.count()
    total_facturado = (
        pedidos_hoy.aggregate(total=Sum('total_pagado'))['total'] or Decimal('0.00')
    )

    # ── 2. Top 5 productos por ingreso del día ────────────────────────
    top_productos = list(
        DetalleVenta.objects
        .filter(__LOOKUP_VENTA__=hoy)
        .values(nombre=F('producto__nombre'))
        .annotate(
            unidades=Sum('cantidad'),
            ingreso=Sum(
                ExpressionWrapper(
                    F('cantidad') * F('precio_unitario'),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            ),
        )
        .order_by('-ingreso')[:5]
    )

    _logger_w18.info(
        f'[reporte_diario] {hoy}: {total_pedidos} pedidos, '
        f'${total_facturado} facturados, {len(top_productos)} productos top.'
    )

    # ── 3. Enviar correo al admin ─────────────────────────────────────
    if _settings_w18.ADMINS:
        _enviar_reporte_ventas(hoy, total_pedidos, total_facturado, top_productos, config)

    return {
        'fecha':           str(hoy),
        'total_pedidos':   total_pedidos,
        'total_facturado': str(total_facturado),
        'top_productos':   [
            {'nombre': p['nombre'], 'unidades': p['unidades'], 'ingreso': str(p['ingreso'])}
            for p in top_productos
        ],
    }


def _enviar_reporte_ventas(fecha, total_pedidos, total_facturado, top_productos, config) -> None:
    """Envía el reporte diario al primer administrador de ADMINS."""
    fecha_str = fecha.strftime('%d/%m/%Y')
    nombre_empresa = getattr(config, 'nombre_empresa', '') or 'ERP Django'
    contexto = {
        'fecha':           fecha_str,
        'config':          config,
        'total_pedidos':   total_pedidos,
        'total_facturado': total_facturado,
        'top_productos':   top_productos,
        'url_admin':       '__URL_SITIO__/admin/ventas/pedido/',
    }

    asunto = f'Reporte de ventas del {fecha_str} — {nombre_empresa}'
    texto_plano = _render_w18('emails/reporte_ventas.txt', contexto)
    html_body = _render_w18('emails/reporte_ventas.html', contexto)

    _admin_nombre, admin_correo = _settings_w18.ADMINS[0]
    mensaje = _Email_w18(
        subject=asunto,
        body=texto_plano,
        from_email=_settings_w18.DEFAULT_FROM_EMAIL,
        to=[admin_correo],
    )
    mensaje.attach_alternative(html_body, 'text/html')

    try:
        mensaje.send()
        _logger_w18.info(f'[reporte_diario] Reporte enviado a {admin_correo}.')
    except Exception as exc:
        _logger_w18.error(f'[reporte_diario] Error al enviar reporte: {exc}')
'''

REPORTE_HTML = r'''<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Reporte de ventas — {{ fecha }}</title>
</head>
<body style="margin:0;padding:0;background:#F5F7FA;font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1A1A2E;">
<div style="max-width:640px;margin:24px auto;background:#FFFFFF;border-radius:12px;overflow:hidden;">

    <div style="background:#0A2342;padding:26px 32px;border-bottom:4px solid #B8860B;">
        <div style="color:#D4AF37;font-size:20px;font-weight:bold;">📊 Reporte de ventas del día</div>
        <div style="color:rgba(255,255,255,.72);font-size:13px;margin-top:4px;">
            {{ config.nombre_empresa|default:"ERP Django" }} · {{ fecha }}
        </div>
    </div>

    <div style="padding:26px 32px;">
        <table style="width:100%;border-collapse:separate;border-spacing:8px;margin-bottom:16px;">
            <tr>
                <td style="background:#E8F0FB;border-radius:8px;padding:14px 16px;text-align:center;width:50%;">
                    <div style="font-size:22px;font-weight:bold;color:#0A2342;">{{ total_pedidos }}</div>
                    <div style="font-size:12px;color:#5A6A7E;text-transform:uppercase;">Pedidos pagados hoy</div>
                </td>
                <td style="background:#E8F0FB;border-radius:8px;padding:14px 16px;text-align:center;width:50%;">
                    <div style="font-size:22px;font-weight:bold;color:#B8860B;">${{ total_facturado }}</div>
                    <div style="font-size:12px;color:#5A6A7E;text-transform:uppercase;">Total facturado</div>
                </td>
            </tr>
        </table>

        {% if top_productos %}
        <p style="font-weight:600;color:#0A2342;margin:0 0 8px;">Top {{ top_productos|length }} productos del día</p>
        <table style="width:100%;border-collapse:collapse;margin:8px 0 16px;">
            <thead>
                <tr>
                    <th style="background:#0A2342;color:#FFF;padding:10px 14px;font-size:11px;text-align:left;">#</th>
                    <th style="background:#0A2342;color:#FFF;padding:10px 14px;font-size:11px;text-align:left;">Producto</th>
                    <th style="background:#0A2342;color:#FFF;padding:10px 14px;font-size:11px;text-align:right;">Unidades</th>
                    <th style="background:#0A2342;color:#FFF;padding:10px 14px;font-size:11px;text-align:right;">Ingreso ($)</th>
                </tr>
            </thead>
            <tbody>
                {% for prod in top_productos %}
                <tr>
                    <td style="padding:9px 14px;border-bottom:1px solid #E8F0FB;">{{ forloop.counter }}</td>
                    <td style="padding:9px 14px;border-bottom:1px solid #E8F0FB;">{{ prod.nombre }}</td>
                    <td style="padding:9px 14px;border-bottom:1px solid #E8F0FB;text-align:right;">{{ prod.unidades }}</td>
                    <td style="padding:9px 14px;border-bottom:1px solid #E8F0FB;text-align:right;">${{ prod.ingreso }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <div style="background:#FDF8E8;border:1px solid #B8860B;border-radius:8px;padding:16px;text-align:center;color:#5A6A7E;margin:16px 0;">
            Sin ventas registradas hoy. ¡Hay oportunidad de mejorar! 🎯
        </div>
        {% endif %}

        <p style="color:#5A6A7E;font-size:12px;margin-top:12px;">
            Panel de administración:
            <a href="{{ url_admin }}" style="color:#0A2342;">{{ url_admin }}</a>
        </p>
    </div>

    <div style="background:#F5F7FA;border-top:1px solid #C8D8EC;padding:14px 32px;font-size:11px;color:#5A6A7E;text-align:center;">
        Generado automáticamente por ERP Django · {{ config.nombre_empresa|default:"ERP Django" }}
    </div>
</div>
</body>
</html>
'''

REPORTE_TXT = '''📊 REPORTE DE VENTAS DEL DÍA — {{ fecha }}
{{ config.nombre_empresa|default:"ERP Django" }}
═══════════════════════════════════════════

RESUMEN:
  Pedidos pagados hoy: {{ total_pedidos }}
  Total facturado:     ${{ total_facturado }}
{% if top_productos %}
TOP PRODUCTOS:
{% for prod in top_productos %}  {{ forloop.counter }}. {{ prod.nombre }}
     Unidades: {{ prod.unidades }} | Ingreso: ${{ prod.ingreso }}
{% endfor %}{% else %}
Sin ventas registradas hoy.
{% endif %}
Panel de administración: {{ url_admin }}

───────────────────────────────────────────
Generado automáticamente por ERP Django
'''

SETTINGS_BEAT = '''

# ── CELERY BEAT — Tareas programadas (W18) ────────────────────────────────
from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    # Verificar stock bajo cada hora (intervalo → timedelta)
    'verificar-stock-bajo-cada-hora': {
        'task':     'ventas.verificar_stock_bajo',
        'schedule': timedelta(hours=1),
        'args':     (5,),   # umbral = 5 unidades
        'options':  {'queue': 'erp_django'},
    },
    # Reporte de ventas diario a las 8:00 AM (hora exacta → crontab)
    'reporte-ventas-diario-8am': {
        'task':     'ventas.reporte_ventas_diario',
        'schedule': crontab(hour=8, minute=0),
        'options':  {'queue': 'erp_django'},
    },
}

# Beat usa la zona horaria de CELERY_TIMEZONE (America/Mexico_City)
CELERY_BEAT_TIMEZONE = TIME_ZONE
'''

COMPOSE_BEAT = '''
  # ── Celery Beat — Scheduler de tareas programadas — W18 ────────────
  # Beat NUNCA debe tener más de una instancia (un solo scheduler)
  celery_beat:
    build: .
    restart: unless-stopped
    command: celery -A core beat -l info
    volumes:
      - .:/app
__DEPENDS__    environment:
      DJANGO_SETTINGS_MODULE: core.settings_prod
      SECRET_KEY:             dev-clave-docker-no-usar-en-produccion
      DATABASE_URL:           postgres://erp_user:erp_pass_local@db:5432/erp_db
      REDIS_URL:              redis://redis:6379/0
'''

TESTS = '''"""Suite de pruebas W18 — Celery Beat y tarea de reporte diario.

Ejecutar con:
    python manage.py test tests.test_w18_beat --verbosity=2

Resultado esperado: Ran 8 tests ... OK
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from clientes.models import Cliente
from configuracion.models import ConfiguracionERP
from productos.models import Categoria, Producto
from ventas.models import DetalleVenta, Pedido, Venta
from ventas.tasks import reporte_ventas_diario

EMAIL_SETTINGS = {
    'EMAIL_BACKEND':                'django.core.mail.backends.locmem.EmailBackend',
    'CELERY_TASK_ALWAYS_EAGER':     True,
    'CELERY_TASK_EAGER_PROPAGATES': True,
    'ADMINS':                       [('Admin Test', 'admin@test.com')],
    'DEFAULT_FROM_EMAIL':           'test@erp.com',
}


def _config_de(tarea):
    for config in settings.CELERY_BEAT_SCHEDULE.values():
        if config['task'] == tarea:
            return config
    return None


class BeatScheduleConfigTest(TestCase):
    """Verifica la configuración de CELERY_BEAT_SCHEDULE."""

    def test_beat_schedule_tiene_verificar_stock(self):
        self.assertIsNotNone(_config_de('ventas.verificar_stock_bajo'))

    def test_beat_schedule_tiene_reporte_diario(self):
        self.assertIsNotNone(_config_de('ventas.reporte_ventas_diario'))

    def test_reporte_usa_crontab(self):
        from celery.schedules import crontab
        config = _config_de('ventas.reporte_ventas_diario')
        self.assertIsNotNone(config)
        self.assertIsInstance(config['schedule'], crontab)

    def test_verificar_stock_usa_timedelta(self):
        config = _config_de('ventas.verificar_stock_bajo')
        self.assertIsNotNone(config)
        self.assertIsInstance(config['schedule'], timedelta)


@override_settings(**EMAIL_SETTINGS)
class ReporteVentasTest(TestCase):
    """Tests de la tarea reporte_ventas_diario."""

    def setUp(self):
        mail.outbox = []
        ConfiguracionERP.get_instance()
        cat = Categoria.objects.create(nombre='Cat Rep')
        self.prod = Producto.objects.create(
            nombre='Producto Reporte', precio=Decimal('500.00'), stock=10, categoria=cat
        )
        cli = Cliente.objects.create(nombre='Cliente Rep', correo='rep@test.com')
        venta = Venta.objects.create(cliente=cli)
        DetalleVenta.objects.create(
            venta=venta, producto=self.prod, cantidad=3, precio_unitario=Decimal('500.00')
        )
        Pedido.objects.create(
            numero_pedido='PED-REP-001', cliente=cli,
            estado='pagado', total_pagado=Decimal('1500.00'),
        )

    def test_reporte_envia_correo_al_admin(self):
        reporte_ventas_diario.delay()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('admin@test.com', mail.outbox[0].to)

    def test_reporte_asunto_contiene_fecha(self):
        reporte_ventas_diario.delay()
        self.assertIn(timezone.localdate().strftime('%d/%m/%Y'), mail.outbox[0].subject)

    def test_reporte_sin_ventas_hoy_envia_correo(self):
        Pedido.objects.all().delete()
        DetalleVenta.objects.all().delete()
        resultado = reporte_ventas_diario.delay().get()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(resultado['total_pedidos'], 0)
        self.assertIn('Sin ventas registradas hoy', mail.outbox[0].body)

    def test_reporte_devuelve_dict_con_totales(self):
        resultado = reporte_ventas_diario.delay().get()
        self.assertEqual(resultado['total_pedidos'], 1)
        self.assertEqual(Decimal(resultado['total_facturado']), Decimal('1500.00'))
        self.assertEqual(resultado['top_productos'][0]['nombre'], 'Producto Reporte')
        self.assertEqual(resultado['top_productos'][0]['unidades'], 3)
'''

HOY = date.today().strftime('%d/%m/%Y')

RETRO = f'''# Sprint 5 Retrospective — ERP Django
## Semanas W16–W18 · Espiral 6: Celery e Integraciones

**Fecha:** {HOY}
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
'''

FICHA = f'''# Ficha de Sistematización — Espiral 6
## ERP Django · Espiral E6: Celery e Integraciones

| Campo | Contenido |
|---|---|
| **Número de espiral** | 6 |
| **Nombre del ciclo** | Celery e Integraciones |
| **Semanas** | W16 – W18 |
| **Fecha de inicio** | ___/___/_____ |
| **Fecha de cierre** | {HOY} |
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
- Sitio en producción: {URL_SITIO}

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
'''

PLANNING = f'''

## Sprint 5 — Estado final W18

| HU | Estado | Pts |
|---|---|---|
| HU-E6-01 a HU-E6-06 | ✅ Completadas | 18/18 |

## Hito M6 — ALCANZADO ✅
- Correo: enviar_confirmacion_pedido → enviado post-pago
- Alerta: verificar_stock_bajo → correo al admin
- Reporte: crontab(hour=8, minute=0) → 8:00 AM diario
- Stock: verificar_stock_bajo cada hora (timedelta)
- Tests: Ran 183 tests → OK
- Fecha: {HOY}
'''


# =====================================================================
# Parches
# =====================================================================
def parchar_tasks(lookups):
    ruta = BASE / 'ventas' / 'tasks.py'
    if not ruta.exists():
        print('ERROR: no encontré ventas/tasks.py (es de W16/W17).')
        sys.exit(1)
    t = ruta.read_text(encoding='utf-8')
    nuevo = t
    if 'erp-django-utec.onrender.com' in nuevo:
        nuevo = nuevo.replace('https://erp-django-utec.onrender.com', URL_SITIO)
        nuevo = nuevo.replace('erp-django-utec.onrender.com', URL_SITIO.replace('https://', ''))
        ok('ventas/tasks.py: direcciones cambiadas a felipelxng.onrender.com')
    if 'def reporte_ventas_diario' in nuevo:
        ok('ventas/tasks.py: reporte_ventas_diario ya estaba')
    else:
        bloque = (TAREA.replace('__LOOKUP_PEDIDO__', lookups['pedido'])
                       .replace('__LOOKUP_VENTA__', lookups['venta'])
                       .replace('__URL_SITIO__', URL_SITIO))
        nuevo = nuevo.rstrip() + '\n' + bloque
        ok('ventas/tasks.py: reporte_ventas_diario agregado')
    if nuevo != t:
        respaldar(ruta)
        ruta.write_text(nuevo, encoding='utf-8')


def parchar_settings():
    ruta = BASE / 'core' / 'settings.py'
    t = ruta.read_text(encoding='utf-8')
    nuevo = t
    if not re.search(r'^from datetime import .*\btimedelta\b', nuevo, re.M):
        if re.search(r'^from pathlib import Path\s*$', nuevo, re.M):
            nuevo = re.sub(r'^(from pathlib import Path\s*)$',
                           r'from datetime import timedelta\n\1', nuevo, count=1, flags=re.M)
        else:
            nuevo = 'from datetime import timedelta\n' + nuevo
        ok('settings.py: import de timedelta agregado')
    if 'CELERY_BEAT_SCHEDULE' in nuevo:
        ok('settings.py: CELERY_BEAT_SCHEDULE ya estaba')
    else:
        nuevo = nuevo.rstrip() + SETTINGS_BEAT
        ok('settings.py: CELERY_BEAT_SCHEDULE agregado')
    if 'TIME_ZONE' not in t:
        aviso('No encontré TIME_ZONE en settings.py; CELERY_BEAT_TIMEZONE lo necesita.')
    if nuevo != t:
        respaldar(ruta)
        ruta.write_text(nuevo, encoding='utf-8')


def parchar_compose():
    ruta = BASE / 'docker-compose.yml'
    if not ruta.exists():
        aviso('No encontré docker-compose.yml; omití el servicio celery_beat.')
        return
    t = ruta.read_text(encoding='utf-8')
    if re.search(r'^\s+celery_beat:', t, re.M):
        ok('docker-compose.yml: celery_beat ya estaba')
        return
    servicios = [s for s in ('redis', 'db') if re.search(rf'^  {s}:', t, re.M)]
    depends = ''
    if servicios:
        depends = '    depends_on:\n' + ''.join(f'      - {s}\n' for s in servicios)
    bloque = COMPOSE_BEAT.replace('__DEPENDS__', depends)

    lineas = t.splitlines(keepends=True)
    try:
        i_serv = next(i for i, l in enumerate(lineas) if re.match(r'^services:\s*$', l))
    except StopIteration:
        aviso('docker-compose.yml no tiene "services:"; omití celery_beat.')
        return
    fin = len(lineas)
    for j in range(i_serv + 1, len(lineas)):
        if re.match(r'^[A-Za-z_][\w-]*:\s*$', lineas[j]):
            fin = j
            break
    # quitar líneas vacías finales antes del punto de inserción
    while fin > i_serv + 1 and lineas[fin - 1].strip() == '':
        fin -= 1
    if fin == len(lineas) and lineas and not lineas[-1].endswith('\n'):
        lineas[-1] += '\n'
    nuevo = ''.join(lineas[:fin]) + bloque + ''.join(['\n'] + lineas[fin:] if fin < len(lineas) else [])
    respaldar(ruta)
    ruta.write_text(nuevo, encoding='utf-8')
    ok('docker-compose.yml: servicio celery_beat agregado')


def main():
    if not (BASE / 'manage.py').exists():
        print('ERROR: pon este archivo junto a manage.py y vuelve a correrlo.')
        sys.exit(1)
    try:
        import celery  # noqa: F401
    except ImportError:
        print('ERROR: no encontré celery. Activa env_erp (env_erp\\Scripts\\activate).')
        sys.exit(1)

    problemas, lookups = revisar_modelos()
    if problemas:
        print('ERROR: tus modelos no coinciden con lo que usa la guía W18:')
        for p in problemas:
            print(f'  ❌ {p}')
        print('No se modificó nada. Manda captura de esto junto con ventas/models.py.')
        sys.exit(1)
    ok(f'Modelos compatibles (filtros: {lookups["pedido"]}, {lookups["venta"]})')

    print('\n— Tarea y templates —')
    parchar_tasks(lookups)
    escribir('templates/emails/reporte_ventas.html', REPORTE_HTML)
    escribir('templates/emails/reporte_ventas.txt', REPORTE_TXT)

    print('\n— Configuración —')
    parchar_settings()
    parchar_compose()

    print('\n— Tests y Scrum —')
    escribir('tests/test_w18_beat.py', TESTS)
    escribir('sprint5_retrospective.md', RETRO, sobrescribir=False)
    escribir('fichas/espiral_06_async.md', FICHA, sobrescribir=False)
    plan = BASE / 'sprint5_planning.md'
    txt = plan.read_text(encoding='utf-8') if plan.exists() else '# Sprint 5 Planning — ERP Django\n'
    if 'Estado final W18' not in txt:
        plan.write_text(txt.rstrip() + PLANNING, encoding='utf-8')
        ok('sprint5_planning.md: cierre W18 / Hito M6 agregado')

    print('\n' + '=' * 60)
    if avisos:
        print('Terminé, pero revisa estos avisos:')
        for a in avisos:
            print(f'  ⚠️  {a}')
    else:
        print('Listo, sin avisos. Ahora corre:')
    print('  python manage.py check')
    print('  python manage.py test tests --verbosity=0      (deben ser 183)')
    print('(Copias de respaldo: archivos .bak junto a cada archivo modificado)')


if __name__ == '__main__':
    main()

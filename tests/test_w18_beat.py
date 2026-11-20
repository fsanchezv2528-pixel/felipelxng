"""Suite de pruebas W18 — Celery Beat y tarea de reporte diario.

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

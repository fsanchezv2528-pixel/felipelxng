# ventas/tasks.py
"""Tareas asíncronas de la app ventas — W17.

Envío real de correos con django-anymail + SendGrid.
Idempotencia: Pedido.correo_enviado=True evita correos duplicados.
"""
import logging
from decimal import Decimal, InvalidOperation

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

logger = logging.getLogger(__name__)


def _a_decimal(valor) -> Decimal:
    """Convierte a Decimal sin tronar si el valor es inválido."""
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0')


def _datos_empresa():
    """Nombre y RFC de la empresa; usa valores por defecto si no existen."""
    from configuracion.models import ConfiguracionERP   # import local

    config = ConfiguracionERP.get_instance()
    nombre = getattr(config, 'nombre_empresa', '') or 'ERP Django'
    rfc    = getattr(config, 'rfc', '') or ''
    return nombre, rfc


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='ventas.enviar_confirmacion_pedido',
)
def enviar_confirmacion_pedido(self, pedido_id: int) -> dict:
    """Envía el correo de confirmación de pedido al cliente (idempotente)."""
    from ventas.models import Pedido   # import local

    try:
        pedido = Pedido.objects.select_related('cliente').get(pk=pedido_id)
    except Pedido.DoesNotExist:
        logger.error(f'[correo_confirmacion] Pedido {pedido_id} no existe.')
        return {'estado': 'error', 'razon': 'Pedido no encontrado'}

    # ── Idempotencia ────────────────────────────────────────────────────
    if pedido.correo_enviado:
        logger.info(
            f'[correo_confirmacion] Pedido {pedido.numero_pedido}: '
            f'correo ya enviado — skip.'
        )
        return {
            'estado':    'skip',
            'pedido_id': pedido_id,
            'razon':     'correo ya enviado',
        }

    nombre_empresa, rfc = _datos_empresa()

    # ── Items con subtotales calculados en Python (texto, sin localizar) ─
    items = []
    for it in (pedido.items_snapshot or []):
        precio   = _a_decimal(it.get('precio', '0'))
        cantidad = int(it.get('cantidad', 0) or 0)
        items.append({
            'nombre':   it.get('nombre', ''),
            'precio':   f'{precio:.2f}',
            'cantidad': cantidad,
            'subtotal': f'{precio * cantidad:.2f}',
        })

    base_url = getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')

    contexto = {
        'pedido':         pedido,
        'nombre_empresa': nombre_empresa,
        'rfc':            rfc,
        'items':          items,
        'total':          f'{_a_decimal(pedido.total_pagado):.2f}',
        'fecha':          timezone.localtime().strftime('%d/%m/%Y %H:%M'),
        'url_pedido':     f'{base_url}/catalogo/',
    }

    asunto      = f'Confirmación de pedido {pedido.numero_pedido} — {nombre_empresa}'
    texto_plano = render_to_string('emails/confirmacion_pedido.txt', contexto)
    html_body   = render_to_string('emails/confirmacion_pedido.html', contexto)

    correo_destino = pedido.cliente.correo

    mensaje = EmailMultiAlternatives(
        subject    = asunto,
        body       = texto_plano,
        from_email = settings.DEFAULT_FROM_EMAIL,
        to         = [correo_destino],
    )
    mensaje.attach_alternative(html_body, 'text/html')

    try:
        mensaje.send()
        logger.info(
            f'[correo_confirmacion] Correo enviado a {correo_destino} '
            f'para pedido {pedido.numero_pedido}.'
        )

        pedido.correo_enviado = True
        pedido.save(update_fields=['correo_enviado'])

        return {
            'estado':    'ok',
            'pedido_id': pedido_id,
            'correo':    correo_destino,
        }

    except Exception as exc:
        logger.error(
            f'[correo_confirmacion] Error al enviar correo del pedido '
            f'{pedido.numero_pedido}: {exc}'
        )
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


@shared_task(
    bind=True,
    max_retries=1,
    name='ventas.verificar_stock_bajo',
)
def verificar_stock_bajo(self, umbral: int = 5) -> dict:
    """Verifica productos con stock bajo y avisa al administrador por correo."""
    from productos.models import Producto   # import local

    productos_bajos = list(
        Producto.objects
        .select_related('categoria')
        .filter(activo=True, stock__lt=umbral)
        .values('pk', 'nombre', 'stock', 'categoria__nombre')
        .order_by('stock')
    )

    if not productos_bajos:
        logger.info(f'[stock_bajo] Todos los productos tienen stock >= {umbral}. OK.')
        return {'umbral': umbral, 'total': 0, 'productos_bajos': []}

    logger.warning(
        f'[stock_bajo] {len(productos_bajos)} producto(s) con stock < {umbral}.'
    )

    if settings.ADMINS:
        _enviar_alerta_stock(productos_bajos, umbral)

    return {
        'umbral':          umbral,
        'total':           len(productos_bajos),
        'productos_bajos': productos_bajos,
    }


def _enviar_alerta_stock(productos_bajos: list, umbral: int) -> None:
    """Envía el correo de alerta de stock al primer administrador."""
    base_url = getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')

    contexto = {
        'productos_bajos': productos_bajos,
        'total_productos': len(productos_bajos),
        'umbral':          umbral,
        'fecha':           timezone.localtime().strftime('%d/%m/%Y %H:%M'),
        'url_admin':       f'{base_url}/admin/productos/producto/',
    }

    asunto      = f'[ALERTA] {len(productos_bajos)} producto(s) con stock < {umbral}'
    texto_plano = render_to_string('emails/alerta_stock.txt', contexto)
    html_body   = render_to_string('emails/alerta_stock.html', contexto)

    _nombre_admin, admin_correo = settings.ADMINS[0]

    mensaje = EmailMultiAlternatives(
        subject    = asunto,
        body       = texto_plano,
        from_email = settings.DEFAULT_FROM_EMAIL,
        to         = [admin_correo],
    )
    mensaje.attach_alternative(html_body, 'text/html')

    try:
        mensaje.send()
        logger.info(
            f'[stock_bajo] Alerta enviada a {admin_correo}. '
            f'{len(productos_bajos)} productos.'
        )
    except Exception as exc:
        logger.error(f'[stock_bajo] Error al enviar alerta: {exc}')



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
    pedidos_hoy = Pedido.objects.filter(fecha_pedido__date=hoy, estado='pagado')
    total_pedidos = pedidos_hoy.count()
    total_facturado = (
        pedidos_hoy.aggregate(total=Sum('total_pagado'))['total'] or Decimal('0.00')
    )

    # ── 2. Top 5 productos por ingreso del día ────────────────────────
    top_productos = list(
        DetalleVenta.objects
        .filter(venta__fecha__date=hoy)
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
        'url_admin':       'https://felipelxng.onrender.com/admin/ventas/pedido/',
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

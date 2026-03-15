
# -*- coding: utf-8 -*-
# PyQGIS tool: click on the map and fetch Mapillary image by feature id on demand
# Exposes: activate_click_tool(), deactivate_click_tool(show_message=True),
#          preview_selected_feature(), enable_auto_identify_preview(), disable_auto_identify_preview()

from datetime import datetime, timezone
import json
import urllib.error
import urllib.parse
import urllib.request

from qgis.PyQt.QtCore import QEvent, QObject, Qt
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import QDockWidget, QLabel, QVBoxLayout, QWidget
from qgis.core import (
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsSettings,
    QgsVectorLayer,
)
from qgis.gui import QgsMapToolEmitPoint
from qgis.utils import iface

_qsettings = QgsSettings()
ACCESS_TOKEN = _qsettings.value('mapillary/access_token', '', type=str).strip()

MAPILLARY_IMAGE_LAYER_NAME = 'Mapillary image'
MAPILLARY_ID_FIELD = 'id'
FEATURE_PICK_TOLERANCE_PX = 8
THUMB_MAX_W, THUMB_MAX_H = 900, 600
ALLOWED_REMOTE_URL_SCHEME = 'https'

canvas = None
project = None
preview = None


class _MapillaryIdentifyClickFilter(QObject):
    def eventFilter(self, watched, event):
        try:
            if event.type() != QEvent.Type.MouseButtonRelease:
                return False
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            if not _is_identify_tool_active():
                return False

            click_tool = globals().get('_MAPILLARY_CLICK_TOOL')
            if click_tool is not None and canvas is not None:
                try:
                    if canvas.mapTool() == click_tool:
                        return False
                except Exception:
                    pass

            map_point = _mouse_event_to_map_point(event)
            if map_point is None:
                return False

            try:
                image_id = _find_clicked_image_id(map_point)
            except Exception:
                return False

            _ensure_infrastructure()
            preview['status_label'].setText(f'Identify: Mapillary image {image_id} gevonden...')
            preview['meta_label'].setText('')
            preview['link_label'].setText('')
            _fetch_and_render_image_id(image_id)
        except Exception:
            return False

        return False


def _validate_remote_url(url):
    if not isinstance(url, str):
        raise RuntimeError('Ongeldige URL ontvangen.')

    candidate = url.strip()
    if not candidate:
        raise RuntimeError('Ongeldige URL ontvangen.')

    parsed = urllib.parse.urlsplit(candidate)
    if parsed.scheme.lower() != ALLOWED_REMOTE_URL_SCHEME or not parsed.netloc:
        raise RuntimeError('Alleen absolute https-URLs zijn toegestaan.')

    return parsed.geturl()


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        absolute_redirect_url = urllib.parse.urljoin(req.full_url, newurl)
        safe_redirect_url = _validate_remote_url(absolute_redirect_url)
        return super().redirect_request(req, fp, code, msg, headers, safe_redirect_url)


def _open_remote_url(url, timeout):
    safe_url = _validate_remote_url(url)
    opener = urllib.request.build_opener(_HttpsOnlyRedirectHandler())
    req = urllib.request.Request(safe_url)
    return opener.open(req, timeout=timeout)


def fetch_json(url):
    with _open_remote_url(url, timeout=60) as resp:
        return json.loads(resp.read().decode('utf-8'))


def timestamp_ms_to_year(timestamp_ms):
    try:
        ts_ms = int(timestamp_ms)
        if ts_ms <= 0:
            return None
        return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).year
    except (TypeError, ValueError, OSError, OverflowError):
        pass

    if isinstance(timestamp_ms, str):
        value = timestamp_ms.strip()
        if value:
            if value.endswith('Z'):
                value = value[:-1] + '+00:00'
            try:
                return datetime.fromisoformat(value).year
            except ValueError:
                pass

    return None


def _ensure_access_token():
    global ACCESS_TOKEN
    if not ACCESS_TOKEN:
        ACCESS_TOKEN = _qsettings.value('mapillary/access_token', '', type=str).strip()
    if not ACCESS_TOKEN:
        raise RuntimeError("Geen Mapillary access token gevonden. Zet via Plugins → Mapillary → Mapillary Token… of Settings → Options → Advanced → 'mapillary/access_token'.")
    return ACCESS_TOKEN


def build_image_query_url(image_id):
    token = _ensure_access_token()
    image_id_str = str(image_id).strip()
    if not image_id_str:
        raise RuntimeError('Geen geldige Mapillary image id ontvangen.')

    base = f'https://graph.mapillary.com/{image_id_str}'
    params = [
        f'access_token={token}',
        'fields=id,computed_geometry,compass_angle,captured_at,is_pano,creator{id},thumb_1024_url',
    ]
    return base + '?' + '&'.join(params)


def _ensure_canvas_project():
    global canvas, project

    if canvas is None:
        canvas = iface.mapCanvas()
    if project is None:
        project = QgsProject.instance()


def _is_identify_tool_active():
    try:
        action = iface.actionIdentify()
        return bool(action and action.isChecked())
    except Exception:
        return False


def _mouse_event_to_map_point(event):
    _ensure_canvas_project()

    try:
        if hasattr(event, 'position'):
            pos = event.position()
            px = int(round(pos.x()))
            py = int(round(pos.y()))
        else:
            pos = event.pos()
            px = int(pos.x())
            py = int(pos.y())
        return canvas.getCoordinateTransform().toMapCoordinates(px, py)
    except Exception:
        return None


def enable_auto_identify_preview():
    _ensure_canvas_project()

    if globals().get('_MAPILLARY_IDENTIFY_FILTER') is not None:
        return

    identify_filter = _MapillaryIdentifyClickFilter(canvas.viewport())
    canvas.viewport().installEventFilter(identify_filter)
    globals()['_MAPILLARY_IDENTIFY_FILTER'] = identify_filter


def disable_auto_identify_preview():
    identify_filter = globals().get('_MAPILLARY_IDENTIFY_FILTER')
    if identify_filter is None:
        return

    try:
        if canvas is not None:
            canvas.viewport().removeEventFilter(identify_filter)
    except Exception:
        pass

    globals()['_MAPILLARY_IDENTIFY_FILTER'] = None


def _transform_point(point_xy, source_crs, dest_crs):
    _ensure_canvas_project()

    if source_crs == dest_crs:
        return QgsPointXY(point_xy.x(), point_xy.y())

    xform = QgsCoordinateTransform(source_crs, dest_crs, project.transformContext())
    return xform.transform(point_xy)


def _find_mapillary_image_layer():
    _ensure_canvas_project()

    for layer in project.mapLayers().values():
        if isinstance(layer, QgsVectorLayer) and layer.isValid() and layer.name() == MAPILLARY_IMAGE_LAYER_NAME:
            return layer
    return None


def _get_layer_and_id_index():
    layer = _find_mapillary_image_layer()
    if layer is None:
        raise RuntimeError("Layer 'Mapillary image' niet gevonden. Laad eerst Mapillary Coverage.")

    id_index = layer.fields().indexOf(MAPILLARY_ID_FIELD)
    if id_index < 0:
        raise RuntimeError("Kolom 'id' ontbreekt in laag 'Mapillary image'.")

    return layer, id_index


def _normalize_image_id(raw_value):
    if raw_value is None:
        return ''
    return str(raw_value).strip()


def _selected_image_id_or_error(layer, id_index):
    selected = layer.selectedFeatures()
    if not selected:
        raise RuntimeError("Geen feature geselecteerd in laag 'Mapillary image'.")

    image_id = _normalize_image_id(selected[-1][id_index])
    if not image_id:
        raise RuntimeError("Geselecteerde feature heeft geen geldige 'id'.")

    return image_id


def _find_clicked_image_id(map_point):
    _ensure_canvas_project()

    layer, id_index = _get_layer_and_id_index()

    layer_point = _transform_point(map_point, project.crs(), layer.crs())

    tolerance_project = max(float(canvas.mapUnitsPerPixel()) * FEATURE_PICK_TOLERANCE_PX, 0.0)
    if tolerance_project <= 0:
        tolerance_project = 1.0

    dx_point = _transform_point(
        QgsPointXY(map_point.x() + tolerance_project, map_point.y()),
        project.crs(),
        layer.crs(),
    )
    dy_point = _transform_point(
        QgsPointXY(map_point.x(), map_point.y() + tolerance_project),
        project.crs(),
        layer.crs(),
    )

    tolerance_layer = max(abs(dx_point.x() - layer_point.x()), abs(dy_point.y() - layer_point.y()))
    if tolerance_layer <= 0:
        tolerance_layer = tolerance_project

    search_rect = QgsRectangle(
        layer_point.x() - tolerance_layer,
        layer_point.y() - tolerance_layer,
        layer_point.x() + tolerance_layer,
        layer_point.y() + tolerance_layer,
    )

    request = QgsFeatureRequest().setFilterRect(search_rect)
    request.setSubsetOfAttributes([id_index])

    click_geom = QgsGeometry.fromPointXY(layer_point)
    nearest_id = None
    nearest_dist = None

    for feature in layer.getFeatures(request):
        feature_id = _normalize_image_id(feature[id_index])
        if not feature_id:
            continue

        geom = feature.geometry()
        if geom is None or geom.isEmpty():
            continue

        dist = geom.distance(click_geom)
        if nearest_id is None or dist < nearest_dist:
            nearest_id = feature_id
            nearest_dist = dist

    if nearest_id is None or nearest_dist is None or nearest_dist > tolerance_layer:
        raise RuntimeError(f'Geen Mapillary feature gevonden binnen {FEATURE_PICK_TOLERANCE_PX} px van de klik.')

    return nearest_id


def fetch_image_by_id(image_id):
    data = fetch_json(build_image_query_url(image_id))
    if not isinstance(data, dict):
        raise RuntimeError('Onverwachte API-respons voor Mapillary image id.')

    pid = str(data.get('id') or image_id)
    return {
        'id': pid,
        'captured_at': timestamp_ms_to_year(data.get('captured_at')),
        'compass': float(data.get('compass_angle') or 0.0),
        'is_pano': bool(data.get('is_pano') or False),
        'creator_id': str((data.get('creator') or {}).get('id') or ''),
        'thumb_url': str(data.get('thumb_1024_url') or ''),
        'url': f'https://www.mapillary.com/app/?pKey={pid}&focus=photo',
    }


def _set_fallback_link(image_id):
    preview['link_label'].setText(
        f'<a href="https://www.mapillary.com/app/?pKey={image_id}&focus=photo">Open in Mapillary</a>'
    )


def _fetch_and_render_image_id(image_id):
    preview['status_label'].setText(f'Mapillary image {image_id} ophalen...')

    try:
        result = fetch_image_by_id(image_id)
    except urllib.error.HTTPError as exc:
        err_text = ''
        try:
            err_text = exc.read().decode('utf-8', errors='ignore')
        except Exception:
            err_text = str(exc)
        set_preview_empty(preview, f'HTTP fout {exc.code}: {err_text[:300]}')
        _set_fallback_link(image_id)
        return
    except Exception as exc:
        set_preview_empty(preview, f'Fout bij ophalen Mapillary image {image_id}: {exc}')
        _set_fallback_link(image_id)
        return

    set_preview_result(preview, result)


def fetch_pixmap_from_url(url):
    if not url:
        return None
    try:
        with _open_remote_url(url, timeout=30) as resp:
            data = resp.read()
        pix = QPixmap()
        if pix.loadFromData(data):
            return pix
    except Exception:
        pass
    return None


def create_preview_panel():
    main_window = iface.mainWindow()

    existing = main_window.findChild(QDockWidget, 'MapillaryPreviewDock')
    if existing:
        main_window.removeDockWidget(existing)
        existing.deleteLater()

    dock = QDockWidget('Mapillary Click Preview', main_window)
    dock.setObjectName('MapillaryPreviewDock')

    container = QWidget(dock)
    layout = QVBoxLayout(container)
    layout.setContentsMargins(8, 8, 8, 8)

    status_label = QLabel("Klik/selecteer/identify een Mapillary image-feature om direct preview te laden. Rechterklik: click-only mode stoppen.")
    status_label.setWordWrap(True)
    layout.addWidget(status_label)

    image_label = QLabel('Nog geen preview geladen.')
    image_label.setWordWrap(True)
    image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    image_label.setMinimumSize(320, 220)
    layout.addWidget(image_label, 1)

    meta_label = QLabel('')
    meta_label.setWordWrap(True)
    layout.addWidget(meta_label)

    link_label = QLabel('')
    link_label.setWordWrap(True)
    link_label.setOpenExternalLinks(True)
    layout.addWidget(link_label)

    dock.setWidget(container)
    main_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
    dock.show()

    return {
        'dock': dock,
        'status_label': status_label,
        'image_label': image_label,
        'meta_label': meta_label,
        'link_label': link_label,
    }


def set_preview_empty(preview_panel, status_text):
    preview_panel['status_label'].setText(status_text)
    preview_panel['image_label'].setPixmap(QPixmap())
    preview_panel['image_label'].setText('Geen preview beschikbaar.')
    preview_panel['meta_label'].setText('')
    preview_panel['link_label'].setText('')


def set_preview_result(preview_panel, result):
    preview_panel['status_label'].setText('Mapillary foto opgehaald op basis van id uit de laag.')
    year = result.get('captured_at')
    year_text = str(year) if year is not None else 'onbekend'
    preview_panel['meta_label'].setText(
        f"ID: {result['id']} | Jaar: {year_text} | Kompas: {result['compass']:.1f}° "
        f"| Pano: {result['is_pano']}"
    )

    pix = fetch_pixmap_from_url(result['thumb_url'])
    if pix is not None and not pix.isNull():
        display_pix = pix.scaled(
            THUMB_MAX_W, THUMB_MAX_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        preview_panel['image_label'].setText('')
        preview_panel['image_label'].setPixmap(display_pix)
    else:
        preview_panel['image_label'].setPixmap(QPixmap())
        preview_panel['image_label'].setText('Thumbnail kon niet geladen worden. Gebruik de link hieronder.')

    if result['url']:
        preview_panel['link_label'].setText(f'<a href="{result["url"]}">Open in Mapillary</a>')
    else:
        preview_panel['link_label'].setText('')


def _ensure_infrastructure():
    global preview

    _ensure_canvas_project()

    if preview is None:
        preview = create_preview_panel()


def deactivate_click_tool(show_message=True):
    _ensure_infrastructure()

    click_tool = globals().get('_MAPILLARY_CLICK_TOOL')
    click_handler = globals().get('_MAPILLARY_CLICK_HANDLER')
    previous_tool = globals().get('_MAPILLARY_PREV_TOOL')

    if click_tool is not None and click_handler is not None:
        try:
            click_tool.canvasClicked.disconnect(click_handler)
        except Exception:
            pass

    if click_tool is not None:
        try:
            if canvas.mapTool() == click_tool:
                canvas.unsetMapTool(click_tool)
        except Exception:
            pass

    restored = False
    if previous_tool is not None:
        try:
            canvas.setMapTool(previous_tool)
            restored = True
        except Exception:
            restored = False

    globals()['_MAPILLARY_CLICK_TOOL'] = None
    globals()['_MAPILLARY_CLICK_HANDLER'] = None
    globals()['_MAPILLARY_PREV_TOOL'] = None

    if show_message and preview is not None:
        if restored:
            preview['status_label'].setText('Click-only mode gestopt. Vorig kaartgereedschap is hersteld.')
        else:
            preview['status_label'].setText('Click-only mode gestopt.')
        preview['meta_label'].setText('')
        preview['link_label'].setText('')


def on_canvas_clicked(map_point, mouse_button):
    if mouse_button == Qt.MouseButton.RightButton:
        deactivate_click_tool(show_message=True)
        return

    if mouse_button != Qt.MouseButton.LeftButton:
        return

    _ensure_infrastructure()

    preview['status_label'].setText("Mapillary feature-id zoeken in laag 'Mapillary image'...")
    preview['meta_label'].setText('')
    preview['link_label'].setText('')

    try:
        image_id = _find_clicked_image_id(map_point)
    except Exception as exc:
        set_preview_empty(preview, f'Kon geen bruikbare id uit laag \"Mapillary image\" lezen: {exc}')
        return

    _fetch_and_render_image_id(image_id)


def preview_selected_feature():
    _ensure_infrastructure()

    preview['status_label'].setText("Geselecteerde feature lezen uit laag 'Mapillary image'...")
    preview['meta_label'].setText('')
    preview['link_label'].setText('')

    try:
        layer, id_index = _get_layer_and_id_index()
        image_id = _selected_image_id_or_error(layer, id_index)
    except Exception as exc:
        set_preview_empty(preview, f'Kan geselecteerde feature niet gebruiken: {exc}')
        return

    _fetch_and_render_image_id(image_id)


def activate_click_tool():
    _ensure_infrastructure()

    current_tool = canvas.mapTool()
    old_tool = globals().get('_MAPILLARY_CLICK_TOOL')
    old_handler = globals().get('_MAPILLARY_CLICK_HANDLER')

    if current_tool is not None and current_tool != old_tool:
        globals()['_MAPILLARY_PREV_TOOL'] = current_tool
    elif '_MAPILLARY_PREV_TOOL' not in globals():
        globals()['_MAPILLARY_PREV_TOOL'] = None

    if old_tool is not None and old_handler is not None:
        try:
            old_tool.canvasClicked.disconnect(old_handler)
        except Exception:
            pass

    if old_tool is not None:
        try:
            if canvas.mapTool() == old_tool:
                canvas.unsetMapTool(old_tool)
        except Exception:
            pass

    click_tool = QgsMapToolEmitPoint(canvas)
    click_tool.canvasClicked.connect(on_canvas_clicked)
    canvas.setMapTool(click_tool)

    if preview is not None:
        preview['status_label'].setText("Click-only mode actief. Klik op een Mapillary image-feature (of gebruik Preview Selected). Rechterklik: stoppen en vorig tool herstellen.")

    globals()['_MAPILLARY_CLICK_TOOL'] = click_tool
    globals()['_MAPILLARY_CLICK_HANDLER'] = on_canvas_clicked
    
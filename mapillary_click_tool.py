
# -*- coding: utf-8 -*-
# PyQGIS tool: click on the map and fetch the nearest Mapillary image on demand
# Exposes: activate_click_tool(), deactivate_click_tool(show_message=True)

from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
import json
import urllib.error
import urllib.request

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import QDockWidget, QLabel, QVBoxLayout, QWidget
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsSettings,
)
from qgis.gui import QgsMapToolEmitPoint
from qgis.utils import iface

_qsettings = QgsSettings()
ACCESS_TOKEN = _qsettings.value('mapillary/access_token', '', type=str).strip()

SEARCH_RADIUS_M = 5
QUERY_LIMIT = 200
THUMB_MAX_W, THUMB_MAX_H = 900, 600

canvas = None
project = None
to_wgs84 = None
preview = None


def fetch_json(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode('utf-8'))


def timestamp_ms_to_year(timestamp_ms):
    try:
        ts_ms = int(timestamp_ms)
        if ts_ms <= 0:
            return None
        return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).year
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def haversine_m(lon1, lat1, lon2, lat2):
    earth_radius_m = 6371000.0
    dlon = radians(lon2 - lon1)
    dlat = radians(lat2 - lat1)
    a = sin(dlat / 2.0) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2.0) ** 2
    c = 2.0 * asin(min(1.0, sqrt(a)))
    return earth_radius_m * c


def build_point_query_url(lon, lat, radius_m, limit=QUERY_LIMIT):
    global ACCESS_TOKEN
    if not ACCESS_TOKEN:
        ACCESS_TOKEN = _qsettings.value('mapillary/access_token', '', type=str).strip()
    if not ACCESS_TOKEN:
        raise RuntimeError("Geen Mapillary access token gevonden. Zet via Plugins → Mapillary → Mapillary Token… of Settings → Options → Advanced → 'mapillary/access_token'.")

    lat_delta = radius_m / 111320.0
    cos_lat = max(0.01, abs(cos(radians(lat))))
    lon_delta = radius_m / (111320.0 * cos_lat)

    min_lon = lon - lon_delta
    min_lat = lat - lat_delta
    max_lon = lon + lon_delta
    max_lat = lat + lat_delta

    base = 'https://graph.mapillary.com/images'
    params = [
        f'access_token={ACCESS_TOKEN}',
        'fields=id,computed_geometry,compass_angle,captured_at,is_pano,creator{id},thumb_1024_url',
        f'bbox={min_lon},{min_lat},{max_lon},{max_lat}',
        f'limit={limit}',
    ]
    return base + '?' + '&'.join(params)


def find_nearest_image(lon, lat):
    url = build_point_query_url(lon, lat, radius_m=SEARCH_RADIUS_M, limit=QUERY_LIMIT)
    data = fetch_json(url)
    items = data.get('data', []) or []

    nearest = None
    for it in items:
        geom = it.get('computed_geometry') or {}
        coords = geom.get('coordinates') or []
        if len(coords) < 2:
            continue

        img_lon = float(coords[0])
        img_lat = float(coords[1])
        dist_m = haversine_m(lon, lat, img_lon, img_lat)

        if nearest is None or dist_m < nearest['distance_m']:
            pid = str(it.get('id') or '')
            nearest = {
                'id': pid,
                'captured_at': timestamp_ms_to_year(it.get('captured_at')),
                'compass': float(it.get('compass_angle') or 0.0),
                'is_pano': bool(it.get('is_pano') or False),
                'creator_id': str((it.get('creator') or {}).get('id') or ''),
                'thumb_url': str(it.get('thumb_1024_url') or ''),
                'url': f'https://www.mapillary.com/app/?pKey={pid}&focus=photo',
                'distance_m': dist_m,
                'search_radius_m': SEARCH_RADIUS_M,
                'candidate_count': len(items),
            }

    return nearest


def fetch_pixmap_from_url(url):
    if not url:
        return None
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
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

    status_label = QLabel('Links klikken: dichtstbijzijnde Mapillary foto ophalen. Rechterklik: click-only mode stoppen.')
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
    preview_panel['status_label'].setText(
        f"Gevonden binnen {result['search_radius_m']} m (kandidaten: {result['candidate_count']})."
    )
    preview_panel['meta_label'].setText(
        f"ID: {result['id']} | Jaar: {result['captured_at']} | Kompas: {result['compass']:.1f}° "
        f"| Pano: {result['is_pano']} | Afstand: {result['distance_m']:.1f} m"
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
    global canvas, project, to_wgs84, preview

    if canvas is None:
        canvas = iface.mapCanvas()
    if project is None:
        project = QgsProject.instance()

    if to_wgs84 is None:
        transform_ctx = project.transformContext()
        project_crs = project.crs()
        wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
        to_wgs84 = QgsCoordinateTransform(project_crs, wgs84, transform_ctx)

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

    try:
        point_wgs84 = to_wgs84.transform(map_point)
        lon = float(point_wgs84.x())
        lat = float(point_wgs84.y())
    except Exception as exc:
        set_preview_empty(preview, f'Kon klikpunt niet transformeren naar WGS84: {exc}')
        return

    preview['status_label'].setText(f'Zoeken rond klikpunt lon={lon:.6f}, lat={lat:.6f}...')
    preview['meta_label'].setText('')
    preview['link_label'].setText('')

    try:
        nearest = find_nearest_image(lon, lat)
    except urllib.error.HTTPError as exc:
        err_text = ''
        try:
            err_text = exc.read().decode('utf-8', errors='ignore')
        except Exception:
            err_text = str(exc)
        set_preview_empty(preview, f'HTTP fout {exc.code}: {err_text[:300]}')
        return
    except Exception as exc:
        set_preview_empty(preview, f'Fout bij ophalen Mapillary data: {exc}')
        return

    if nearest is None:
        set_preview_empty(preview, f'Geen Mapillary foto gevonden binnen {SEARCH_RADIUS_M} m van het klikpunt.')
        return

    set_preview_result(preview, nearest)


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
        preview['status_label'].setText('Click-only mode actief. Links klikken: zoeken. Rechterklik: stoppen en vorig tool herstellen.')

    globals()['_MAPILLARY_CLICK_TOOL'] = click_tool
    globals()['_MAPILLARY_CLICK_HANDLER'] = on_canvas_clicked

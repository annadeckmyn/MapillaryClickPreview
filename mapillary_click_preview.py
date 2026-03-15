
# -*- coding: utf-8 -*-
# QGIS Plugin: Mapillary Click Preview (toggle button + add coverage VTP layer)

import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
import requests
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QAction, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QSpinBox,
)
from qgis.core import (
    QgsSettings, QgsProject, QgsVectorLayer,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY,
    QgsMessageLog, Qgis,
)

from . import mapillary_click_tool as tool


_SERVER_URLS = {
    'original': 'https://tiles.mapillary.com/maps/vtp/mly1_public/2/{z}/{x}/{y}?access_token={token}',
    'computed': 'https://tiles.mapillary.com/maps/vtp/mly1_computed_public/2/{z}/{x}/{y}?access_token={token}',
}
LAYER_LEVELS = ['image', 'sequence']
CACHE_EXPIRE_HOURS = 24
_CACHE_EXPIRE = timedelta(hours=CACHE_EXPIRE_HOURS)
MAX_WEB_MERCATOR_LAT = 85.05112878
MAPILLARY_LAUNCH_YEAR = 2012


def _is_finite_number(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _clamp(value, minimum, maximum):
    return max(minimum, min(value, maximum))


def _deg2num(lat_deg, lon_deg, zoom):
    lat_deg = _clamp(float(lat_deg), -MAX_WEB_MERCATOR_LAT, MAX_WEB_MERCATOR_LAT)
    lon_deg = _clamp(float(lon_deg), -180.0, 179.999999999)
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    x = int((lon_deg + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(lat_rad) + (1.0 / math.cos(lat_rad))) / math.pi) / 2.0 * n)
    return x, y


def _zoom_for_pixel_size(pixel_size):
    for i in range(30):
        if pixel_size > (180 / 256.0 / 2 ** i):
            return i - 1 if i != 0 else 0
    return 29


def _get_tile_range(bounds, zoom):
    xm, ym, xmx, ymx = bounds
    start = _deg2num(ymx, xm, zoom)
    end = _deg2num(ym, xmx, zoom)
    return (min(start[0], end[0]), max(start[0], end[0])), (min(start[1], end[1]), max(start[1], end[1]))


def _build_tile_url(x, y, z, template):
    return template.replace('{x}', str(x)).replace('{y}', str(y)).replace('{z}', str(z))


def _get_proxies():
    from qgis.PyQt.QtCore import QSettings
    s = QSettings()
    if s.value('proxy/proxyEnabled', '') != 'true':
        return None
    host = s.value('proxy/proxyHost', '')
    port = s.value('proxy/proxyPort', '')
    user = s.value('proxy/proxyUser', '')
    pwd = s.value('proxy/proxyPassword', '')
    scheme = 'socks5' if s.value('proxy/proxyType', '') == 'Socks5Proxy' else 'http'
    addr = f'{scheme}://{user}:{pwd}@{host}:{port}'
    return {'http': addr, 'https': addr}


def _extend_layer(target, source, name):
    if not target:
        wkb_map = {0: 'UnknownType', 1: 'Point', 2: 'LineString', 3: 'Polygon',
                   4: 'MultiPoint', 5: 'MultiLineString', 6: 'MultiPolygon'}
        geom_type = wkb_map.get(int(source.wkbType()), 'UnknownType')
        crs = source.crs().toWkt()
        target = QgsVectorLayer(f'{geom_type}?crs={crs}', name, 'memory')
        target.dataProvider().addAttributes(source.fields())
        target.updateFields()
    target.dataProvider().addFeatures(list(source.getFeatures()))
    return target


class TokenDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Mapillary Token')
        layout = QFormLayout(self)

        self.edit = QLineEdit(self)
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)

        s = QgsSettings()
        self.edit.setText(s.value('mapillary/access_token', '', type=str))
        layout.addRow('Access token', self.edit)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self
        )
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)

    def _on_accept(self):
        s = QgsSettings()
        s.setValue('mapillary/access_token', self.edit.text().strip())
        self.accept()


def _build_year_filter_expr(from_year, to_year):
    """QGIS expression that filters coverage features by captured_at year range."""
    start_ms = int(datetime(from_year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime(to_year + 1, 1, 1, tzinfo=timezone.utc).timestamp() * 1000) - 1
    return f'captured_at IS NULL OR (captured_at >= {start_ms} AND captured_at <= {end_ms})'


class YearFilterDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Filter: Captured At (Year Range)')
        layout = QFormLayout(self)

        current_year = datetime.now().year
        s = QgsSettings()
        enabled = s.value('mapillary/year_filter_enabled', False, type=bool)
        from_year = s.value('mapillary/year_filter_from', MAPILLARY_LAUNCH_YEAR, type=int)
        to_year = s.value('mapillary/year_filter_to', current_year, type=int)

        self.enabled_check = QCheckBox('Enable year filter', self)
        self.enabled_check.setChecked(enabled)
        layout.addRow(self.enabled_check)

        self.from_spin = QSpinBox(self)
        self.from_spin.setRange(MAPILLARY_LAUNCH_YEAR, current_year)
        self.from_spin.setValue(max(MAPILLARY_LAUNCH_YEAR, min(from_year, current_year)))
        layout.addRow('From year:', self.from_spin)

        self.to_spin = QSpinBox(self)
        self.to_spin.setRange(MAPILLARY_LAUNCH_YEAR, current_year)
        self.to_spin.setValue(max(MAPILLARY_LAUNCH_YEAR, min(to_year, current_year)))
        layout.addRow('To year:', self.to_spin)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self
        )
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)

    def _on_accept(self):
        from_year = self.from_spin.value()
        to_year = self.to_spin.value()
        if from_year > to_year:
            from_year, to_year = to_year, from_year
        s = QgsSettings()
        s.setValue('mapillary/year_filter_enabled', self.enabled_check.isChecked())
        s.setValue('mapillary/year_filter_from', from_year)
        s.setValue('mapillary/year_filter_to', to_year)
        self.accept()


class MapillaryClickPreviewPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.settings_action = None
        self.add_tiles_action = None
        self.add_computed_tiles_action = None
        self.filter_year_action = None
        self.coverage_tile_set = None
        self.coverage_range_key = None
        self.coverage_layers = {level: None for level in LAYER_LEVELS}
        self.coverage_refreshing = False
        self._auto_preview_layer = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), 'icon.svg')
        icon = QIcon(icon_path) if os.path.exists(icon_path) else self.iface.mainWindow().style().standardIcon(3)

        self.action = QAction(icon, 'Mapillary Click Preview', self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setToolTip('Toggle: Click-only mode aan/uit (links klikken = zoeken, rechts = stoppen)')
        self.action.toggled.connect(self._on_toggled)

        self.settings_action = QAction('Mapillary Token…', self.iface.mainWindow())
        self.settings_action.triggered.connect(self._open_settings)

        self.add_tiles_action = QAction('Load Mapillary Coverage (Original)', self.iface.mainWindow())
        self.add_tiles_action.triggered.connect(
            lambda: self._load_coverage('original', force=True))

        self.add_computed_tiles_action = QAction('Load Mapillary Coverage (Computed)', self.iface.mainWindow())
        self.add_computed_tiles_action.triggered.connect(
            lambda: self._load_coverage('computed', force=True))

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu('&Mapillary', self.action)
        self.iface.addPluginToMenu('&Mapillary', self.settings_action)
        self.iface.addPluginToMenu('&Mapillary', self.add_tiles_action)
        self.iface.addPluginToMenu('&Mapillary', self.add_computed_tiles_action)

        self.filter_year_action = QAction('Filter Mapillary Coverage by Year…', self.iface.mainWindow())
        self.filter_year_action.triggered.connect(self._open_year_filter)
        self.iface.addPluginToMenu('&Mapillary', self.filter_year_action)

        try:
            tool.enable_auto_identify_preview()
        except Exception as e:
            QgsMessageLog.logMessage(f'Could not enable auto identify preview: {e}', 'Mapillary', Qgis.Warning)

        try:
            self.iface.mapCanvas().mapCanvasRefreshed.connect(self._refresh_coverage_for_canvas)
        except Exception:
            pass

    def unload(self):
        try:
            self.iface.mapCanvas().mapCanvasRefreshed.disconnect(self._refresh_coverage_for_canvas)
        except Exception:
            pass

        try:
            if self.action:
                self.iface.removeToolBarIcon(self.action)
                self.iface.removePluginMenu('&Mapillary', self.action)
            if self.settings_action:
                self.iface.removePluginMenu('&Mapillary', self.settings_action)
            if self.add_tiles_action:
                self.iface.removePluginMenu('&Mapillary', self.add_tiles_action)
            if self.add_computed_tiles_action:
                self.iface.removePluginMenu('&Mapillary', self.add_computed_tiles_action)
            if self.filter_year_action:
                self.iface.removePluginMenu('&Mapillary', self.filter_year_action)
        except Exception:
            pass

        try:
            tool.deactivate_click_tool(show_message=False)
        except Exception:
            pass

        try:
            tool.disable_auto_identify_preview()
        except Exception:
            pass

        self.coverage_tile_set = None
        self.coverage_range_key = None
        self._remove_coverage_layers()

    def _on_toggled(self, checked):
        try:
            if checked:
                tool.activate_click_tool()
            else:
                tool.deactivate_click_tool(show_message=False)
        except Exception as e:
            self.action.setChecked(False)
            QgsMessageLog.logMessage(f'Error toggling Mapillary tool: {e}', 'Mapillary', Qgis.Critical)

    def _open_settings(self):
        dlg = TokenDialog(self.iface.mainWindow())
        dlg.setModal(True)
        dlg.exec()

    def _on_image_layer_selection_changed(self, selected, deselected, clear_and_select):
        layer = self._auto_preview_layer
        if layer is None:
            return

        try:
            if layer.selectedFeatureCount() <= 0:
                return
            tool.preview_selected_feature()
        except Exception as e:
            QgsMessageLog.logMessage(f'Auto preview on selection failed: {e}', 'Mapillary', Qgis.Warning)

    def _connect_auto_preview_layer(self, layer):
        if layer is self._auto_preview_layer:
            return

        self._disconnect_auto_preview_layer()

        if layer is None or not layer.isValid():
            return

        try:
            layer.selectionChanged.connect(self._on_image_layer_selection_changed)
            self._auto_preview_layer = layer
        except Exception as e:
            QgsMessageLog.logMessage(f'Could not connect selection auto-preview: {e}', 'Mapillary', Qgis.Warning)
            self._auto_preview_layer = None

    def _disconnect_auto_preview_layer(self):
        if self._auto_preview_layer is None:
            return

        try:
            self._auto_preview_layer.selectionChanged.disconnect(self._on_image_layer_selection_changed)
        except Exception:
            pass

        self._auto_preview_layer = None

    def _open_year_filter(self):
        dlg = YearFilterDialog(self.iface.mainWindow())
        dlg.setModal(True)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._apply_year_filter_to_existing_layers()

    def _refresh_coverage_for_canvas(self):
        if not self.coverage_tile_set or self.coverage_refreshing:
            return
        self._load_coverage(self.coverage_tile_set)

    def _remove_coverage_layers(self):
        self._disconnect_auto_preview_layer()

        for level in LAYER_LEVELS:
            layer = self.coverage_layers.get(level)
            if not layer:
                continue
            try:
                QgsProject.instance().removeMapLayer(layer.id())
            except Exception:
                pass
            self.coverage_layers[level] = None

    def _apply_year_filter_to_existing_layers(self):
        s = QgsSettings()
        enabled = s.value('mapillary/year_filter_enabled', False, type=bool)
        year_expr = ''
        if enabled:
            from_year = s.value('mapillary/year_filter_from', MAPILLARY_LAUNCH_YEAR, type=int)
            to_year = s.value('mapillary/year_filter_to', datetime.now().year, type=int)
            year_expr = _build_year_filter_expr(from_year, to_year)

        target_layer_names = {'Mapillary image', 'Mapillary sequence'}

        for layer in QgsProject.instance().mapLayers().values():
            if not (isinstance(layer, QgsVectorLayer) and layer.name() in target_layer_names):
                continue

            subset = ''
            if enabled and layer.fields().lookupField('captured_at') != -1:
                subset = year_expr

            layer.setSubsetString(subset)
            layer.triggerRepaint()

    def _load_coverage(self, tile_set='original', force=False):
        s = QgsSettings()
        token = s.value('mapillary/access_token', '', type=str).strip()
        if not token:
            self._open_settings()
            token = s.value('mapillary/access_token', '', type=str).strip()
            if not token:
                QgsMessageLog.logMessage('No Mapillary token set.', 'Mapillary', Qgis.Warning)
                return

        server_url = _SERVER_URLS[tile_set].replace('{token}', token)

        canvas = self.iface.mapCanvas()
        crs_src = canvas.mapSettings().destinationCrs()
        crs_wgs84 = QgsCoordinateReferenceSystem(4326)
        xform = QgsCoordinateTransform(crs_src, crs_wgs84, QgsProject.instance())

        ex = canvas.extent()
        wgs84_min = xform.transform(QgsPointXY(ex.xMinimum(), ex.yMinimum()))
        wgs84_max = xform.transform(QgsPointXY(ex.xMaximum(), ex.yMaximum()))
        bounds = (wgs84_min.x(), wgs84_min.y(), wgs84_max.x(), wgs84_max.y())

        if not all(_is_finite_number(v) for v in bounds):
            QgsMessageLog.logMessage('Canvas extent is not valid for tile loading.', 'Mapillary', Qgis.Warning)
            return

        canvas_width = canvas.width()
        if canvas_width <= 0:
            return
        map_units_per_pixel = abs(wgs84_max.x() - wgs84_min.x()) / canvas_width
        zoom_level = _zoom_for_pixel_size(map_units_per_pixel)
        zoom_level = max(0, min(int(zoom_level), 14))

        try:
            x_range, y_range = _get_tile_range(bounds, zoom_level)
        except (ValueError, OverflowError) as e:
            QgsMessageLog.logMessage(f'Could not compute tile range: {e}', 'Mapillary', Qgis.Warning)
            return

        range_key = (tile_set, zoom_level, x_range[0], x_range[1], y_range[0], y_range[1])
        if not force and range_key == self.coverage_range_key:
            return

        self.coverage_refreshing = True
        try:
            cache_dir = os.path.join(tempfile.gettempdir(), 'go2mapillary')
            layers = {level: None for level in LAYER_LEVELS}

            for x in range(x_range[0], x_range[1] + 1):
                for y in range(y_range[0], y_range[1] + 1):
                    folder = os.path.join(cache_dir, str(zoom_level), str(x))
                    os.makedirs(folder, exist_ok=True)
                    mvt_path = os.path.join(folder, f'{y}.mvt')

                    expired = (
                        not os.path.exists(mvt_path) or datetime.fromtimestamp(os.path.getmtime(mvt_path)) < (datetime.now() - _CACHE_EXPIRE)
                    )
                    if expired:
                        url = _build_tile_url(x, y, zoom_level, server_url)
                        try:
                            resp = requests.get(url, proxies=_get_proxies(), timeout=15)
                            resp.raise_for_status()
                            with open(mvt_path, 'wb') as f:
                                f.write(resp.content)
                        except Exception as e:
                            QgsMessageLog.logMessage(
                                f'Tile download failed [{x},{y},{zoom_level}]: {e}', 'Mapillary', Qgis.Warning)
                            continue

                    if os.path.exists(mvt_path):
                        for level in LAYER_LEVELS:
                            tile = QgsVectorLayer(f'{mvt_path}|layername={level}', level, 'ogr')
                            if tile.isValid():
                                layers[level] = _extend_layer(layers[level], tile, f'Mapillary {level}')

            self._remove_coverage_layers()

            added = []
            for level in LAYER_LEVELS:
                lyr = layers[level]
                if lyr and lyr.isValid():
                    qml_candidates = [os.path.join(os.path.dirname(__file__), 'res', f'mapillary_{level}.qml')]

                    for qml in qml_candidates:
                        if os.path.exists(qml):
                            lyr.loadNamedStyle(qml)
                            break
                    QgsProject.instance().addMapLayer(lyr)
                    self.coverage_layers[level] = lyr
                    added.append(lyr)

            self.coverage_tile_set = tile_set
            self.coverage_range_key = range_key

            if added:
                self._apply_year_filter_to_existing_layers()
                self._connect_auto_preview_layer(self.coverage_layers.get('image'))
                QgsMessageLog.logMessage(
                    f'Mapillary coverage loaded: {len(added)} layer(s) at zoom {zoom_level}.',
                    'Mapillary', Qgis.Info)
            else:
                self._disconnect_auto_preview_layer()
                QgsMessageLog.logMessage(
                    'No Mapillary coverage tiles found for current extent.', 'Mapillary', Qgis.Warning)
        finally:
            self.coverage_refreshing = False

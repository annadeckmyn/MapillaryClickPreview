
# -*- coding: utf-8 -*-
# QGIS Plugin: Mapillary Click Preview (toggle button + add coverage VTP layer)

import json
import os
from datetime import datetime, timezone
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QAction, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QSpinBox,
)
from qgis.core import QgsSettings, QgsProject, QgsVectorTileBasicRenderer, QgsVectorTileLayer

from . import mapillary_click_tool as tool


TILES_TEMPLATE = 'https://tiles.mapillary.com/maps/vtp/mly1_public/2/{{z}}/{{x}}/{{y}}?access_token={token}'
MAPILLARY_LAUNCH_YEAR = 2012


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
    """QGIS expression that filters VT image features by captured_at year range.
    Features without captured_at (sequences, overviews) are always passed through."""
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
        self.filter_year_action = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), 'icon.svg')
        icon = QIcon(icon_path) if os.path.exists(icon_path) else self.iface.mainWindow().style().standardIcon(3)

        self.action = QAction(icon, 'Mapillary Click Preview', self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setToolTip('Toggle: Click-only mode aan/uit (links klikken = zoeken, rechts = stoppen)')
        self.action.toggled.connect(self._on_toggled)

        self.settings_action = QAction('Mapillary Token…', self.iface.mainWindow())
        self.settings_action.triggered.connect(self._open_settings)

        self.add_tiles_action = QAction('Add Mapillary Coverage (Vector Tiles)', self.iface.mainWindow())
        self.add_tiles_action.triggered.connect(self._add_vector_tiles_layer)

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu('&Mapillary', self.action)
        self.iface.addPluginToMenu('&Mapillary', self.settings_action)
        self.iface.addPluginToMenu('&Mapillary', self.add_tiles_action)

        self.filter_year_action = QAction('Filter Mapillary Coverage by Year…', self.iface.mainWindow())
        self.filter_year_action.triggered.connect(self._open_year_filter)
        self.iface.addPluginToMenu('&Mapillary', self.filter_year_action)

    def unload(self):
        try:
            if self.action:
                self.iface.removeToolBarIcon(self.action)
                self.iface.removePluginMenu('&Mapillary', self.action)
            if self.settings_action:
                self.iface.removePluginMenu('&Mapillary', self.settings_action)
            if self.add_tiles_action:
                self.iface.removePluginMenu('&Mapillary', self.add_tiles_action)
            if self.filter_year_action:
                self.iface.removePluginMenu('&Mapillary', self.filter_year_action)
        except Exception:
            pass

        try:
            tool.deactivate_click_tool(show_message=False)
        except Exception:
            pass

    def _on_toggled(self, checked):
        try:
            if checked:
                tool.activate_click_tool()
            else:
                tool.deactivate_click_tool(show_message=False)
        except Exception as e:
            self.action.setChecked(False)
            from qgis.core import QgsMessageLog, Qgis
            QgsMessageLog.logMessage(f'Error toggling Mapillary tool: {e}', 'Mapillary', Qgis.Critical)

    def _open_settings(self):
        dlg = TokenDialog(self.iface.mainWindow())
        dlg.setModal(True)
        dlg.exec()

    def _open_year_filter(self):
        dlg = YearFilterDialog(self.iface.mainWindow())
        dlg.setModal(True)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._apply_year_filter_to_existing_layers()

    def _apply_year_filter_to_existing_layers(self):
        s = QgsSettings()
        enabled = s.value('mapillary/year_filter_enabled', False, type=bool)
        year_expr = ''
        if enabled:
            from_year = s.value('mapillary/year_filter_from', MAPILLARY_LAUNCH_YEAR, type=int)
            to_year = s.value('mapillary/year_filter_to', datetime.now().year, type=int)
            year_expr = _build_year_filter_expr(from_year, to_year)

        _ORIG_KEY = 'mapillary_year_filter_originals'
        for layer in QgsProject.instance().mapLayers().values():
            if not (isinstance(layer, QgsVectorTileLayer) and layer.name() == 'Mapillary Coverage (mly1_public)'):
                continue
            renderer = layer.renderer()
            if not isinstance(renderer, QgsVectorTileBasicRenderer):
                continue
            if hasattr(renderer, 'styles'):
                rules = renderer.styles()
            else:
                rules = renderer.rules()

            # Persist original (pre-filter) rule expressions on first encounter
            saved_raw = layer.customProperty(_ORIG_KEY)
            if saved_raw:
                try:
                    originals = json.loads(saved_raw)
                except Exception:
                    originals = [r.filterExpression() for r in rules]
                    layer.setCustomProperty(_ORIG_KEY, json.dumps(originals))
            else:
                originals = [r.filterExpression() for r in rules]
                layer.setCustomProperty(_ORIG_KEY, json.dumps(originals))

            for i, rule in enumerate(rules):
                base = originals[i] if i < len(originals) else ''
                if year_expr:
                    new_filter = f'({base}) AND ({year_expr})' if base else year_expr
                else:
                    new_filter = base
                rule.setFilterExpression(new_filter)

            if hasattr(renderer, 'setStyles'):
                renderer.setStyles(rules)
            else:
                renderer.setRules(rules)
            layer.triggerRepaint()

    def _add_vector_tiles_layer(self):
        s = QgsSettings()
        token = s.value('mapillary/access_token', '', type=str).strip()
        if not token:
            # Prompt for token first
            self._open_settings()
            token = s.value('mapillary/access_token', '', type=str).strip()
            if not token:
                from qgis.core import QgsMessageLog, Qgis
                QgsMessageLog.logMessage('No Mapillary token set. Cannot add vector tiles layer.', 'Mapillary', Qgis.Warning)
                return

        url = TILES_TEMPLATE.format(token=token)
        # QGIS Vector Tile URI uses provider options style: type=xyz&url=... (see docs)
        uri = f'type=xyz&url={url}'
        vtl = QgsVectorTileLayer(uri, 'Mapillary Coverage (mly1_public)')
        if not vtl.isValid():
            from qgis.core import QgsMessageLog, Qgis
            QgsMessageLog.logMessage('Failed to create Mapillary coverage vector tile layer. Check URL/token.', 'Mapillary', Qgis.Critical)
            return
        QgsProject.instance().addMapLayer(vtl)
        self._apply_year_filter_to_existing_layers()
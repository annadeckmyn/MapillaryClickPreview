
# -*- coding: utf-8 -*-
# QGIS Plugin: Mapillary Click Preview (toggle button + add coverage VTP layer)

import os
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QDialog, QFormLayout, QLineEdit, QDialogButtonBox
from qgis.core import QgsSettings, QgsProject, QgsVectorTileLayer

from . import mapillary_click_tool as tool


TILES_TEMPLATE = 'https://tiles.mapillary.com/maps/vtp/mly1_public/2/{{z}}/{{x}}/{{y}}?access_token={token}'


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


class MapillaryClickPreviewPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.settings_action = None
        self.add_tiles_action = None

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

    def unload(self):
        try:
            if self.action:
                self.iface.removeToolBarIcon(self.action)
                self.iface.removePluginMenu('&Mapillary', self.action)
            if self.settings_action:
                self.iface.removePluginMenu('&Mapillary', self.settings_action)
            if self.add_tiles_action:
                self.iface.removePluginMenu('&Mapillary', self.add_tiles_action)
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

# -*- coding: utf-8 -*-
# Plugin bootstrap

def classFactory(iface):
    from .mapillary_click_preview import MapillaryClickPreviewPlugin
    return MapillaryClickPreviewPlugin(iface)

# Mapillary Click Preview

QGIS plugin to load Mapillary coverage and preview the nearest Mapillary image from a map click.

## What It Does

- Adds a toggle tool for click-only preview on the map canvas.
- Loads Mapillary coverage as vector layers (`image`, `sequence`) from:
  - `mly1_public` (original)
  - `mly1_computed_public` (computed)
- Opens a docked preview panel with:
  - thumbnail
  - image metadata (ID, year, compass, pano flag)
  - link to open the image in Mapillary
- Supports auto-preview when:
  - selecting a feature in the `Mapillary image` layer
  - clicking with QGIS Identify mode
- Includes an optional year filter (`captured_at`) for coverage layers.

## Requirements

- QGIS `>= 3.44` and `< 5.0` (from plugin metadata; includes QGIS 3.44.7)
- Internet access for Mapillary API and tiles
- A Mapillary access token

## Installation (From Source)

1. Copy this folder into your QGIS plugin directory as `MapillaryClickPreview`.
2. Restart QGIS.
3. Enable the plugin in `Plugins > Manage and Install Plugins`.

Typical plugin directories:

- Windows (QGIS 3.x): `%APPDATA%\\QGIS\\QGIS3\\profiles\\default\\python\\plugins`
- Windows (QGIS 4.x): `%APPDATA%\\QGIS\\QGIS4\\profiles\\default\\python\\plugins`
- Linux (QGIS 3.x): `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins`
- Linux (QGIS 4.x): `~/.local/share/QGIS/QGIS4/profiles/default/python/plugins`
- macOS (QGIS 3.x): `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins`
- macOS (QGIS 4.x): `~/Library/Application Support/QGIS/QGIS4/profiles/default/python/plugins`

## First-Time Setup

1. Open `Plugins > Mapillary > Mapillary Token...`
2. Paste your Mapillary access token and save.

The token is stored in QGIS settings under:

- `mapillary/access_token`

## Usage

1. Load coverage:
   - `Plugins > Mapillary > Load Mapillary Coverage (Original)`
   - or `Plugins > Mapillary > Load Mapillary Coverage (Computed)`
2. Optional: set year filtering via `Plugins > Mapillary > Filter Mapillary Coverage by Year...`
3. Start click-only preview using the toolbar button `Mapillary Click Preview`.
4. Left-click near an image feature to fetch and preview the nearest image.
5. Right-click to stop click-only mode and restore the previous map tool.

Notes:

- Coverage refreshes with map canvas extent changes.
- Downloaded `.mvt` tiles are cached in `%TEMP%\\go2mapillary` for up to 24 hours.

## Menu Actions

- `Mapillary Click Preview` (toggle tool)
- `Mapillary Token...`
- `Load Mapillary Coverage (Original)`
- `Load Mapillary Coverage (Computed)`
- `Filter Mapillary Coverage by Year...`

## Troubleshooting

- If coverage does not load, verify your access token and internet connection.
- If preview fails, make sure a valid `Mapillary image` layer is present and clicked/selected.
- If thumbnails fail to render, use the "Open in Mapillary" link shown in the preview dock.

## Development Notes

- Main plugin entry point: `mapillary_click_preview.py`
- Click and preview logic: `mapillary_click_tool.py`
- Layer styling: `res/mapillary_image.qml`, `res/mapillary_sequence.qml`

## License

This project is licensed under GNU GPL v2 or later.
See `LICENSE` for full text.

## Disclaimer

This plugin is an independent tool and is not officially affiliated with Mapillary or Meta.
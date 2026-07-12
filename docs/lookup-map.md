# Lookup Page and Station Origin Map

## Automatic updates

The Lookup page updates its live information in place every 15 seconds. The browser page is not reloaded. Overlapping requests are blocked, and automatic updates pause while the tab is hidden. The displayed updated time is formatted in the viewer's browser-local time zone.

## Map interaction

Select **View Station Origin Map** to open the map. The button remains white while the map is open. Each orange marker represents a currently listed station with an available approximate location. Select a marker to view its callsign, operator name when available, and city/region.

The map uses Esri Dark Gray Canvas tiles and automatically fits the available station markers. Locations that cannot be resolved are listed below the map.

## Location sources

Location resolution uses this order:

1. QRZ XML callbook coordinates, when valid QRZ username and password information is saved under **Reimagined Settings → Lookup / Map**.
2. Public city/region text from the local AllStar node database, geocoded through Nominatim when QRZ data is unavailable.

QRZ credentials remain in `/etc/allscan-reimagined/secrets.json` and are never returned to the browser. Browser-visible coordinates are rounded so the map shows an approximate area instead of a precise address.

Fallback geocoding is deliberately gradual. ASR requests no more than one uncached public location every 15 seconds, serializes concurrent requests, and caches successful fallback results for 90 days. This keeps the map partially functional without QRZ access while limiting load on the node and the public geocoding service.

The station map cache is stored at `/etc/allscan-reimagined/station-map-cache.json` and survives ASR package updates.

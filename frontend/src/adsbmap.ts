// Aircraft map for ADS-B mode (Leaflet + OpenStreetMap tiles).
//
// Plots each aircraft with a position as a heading-rotated marker labelled with
// its callsign + altitude. Stale aircraft (dropped by the backend) are removed.

import L from "leaflet";
import "leaflet/dist/leaflet.css";

interface Aircraft {
  icao: string;
  flight?: string;
  alt?: number;
  speed?: number;
  track?: number;
  lat?: number;
  lon?: number;
}

export class AdsbMap {
  private map: L.Map | null = null;
  private markers = new Map<string, L.Marker>();
  private didAutoCenter = false;

  // Create the map the first time ADS-B mode is shown; re-fit if already created.
  ensure(containerId: string): void {
    if (this.map) {
      this.map.invalidateSize();
      return;
    }
    this.map = L.map(containerId, { zoomControl: true, attributionControl: true })
      .setView([59.33, 18.06], 7); // default: Stockholm, Sweden
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: "© OpenStreetMap",
    }).addTo(this.map);
    // Leaflet measures the container on creation; it may be 0 until visible.
    setTimeout(() => this.map?.invalidateSize(), 0);
  }

  update(list: Aircraft[]): void {
    if (!this.map) return;
    const seen = new Set<string>();
    for (const ac of list) {
      if (typeof ac.lat !== "number" || typeof ac.lon !== "number") continue;
      seen.add(ac.icao);
      const label =
        (ac.flight || ac.icao) + (ac.alt != null ? ` · ${ac.alt} ft` : "");
      const icon = L.divIcon({
        className: "plane-icon",
        html:
          `<div class="plane-rot" style="transform:rotate(${ac.track ?? 0}deg)">✈</div>` +
          `<span class="plane-label">${label}</span>`,
        iconSize: [22, 22],
        iconAnchor: [11, 11],
      });
      let m = this.markers.get(ac.icao);
      if (!m) {
        m = L.marker([ac.lat, ac.lon], { icon }).addTo(this.map);
        this.markers.set(ac.icao, m);
      } else {
        m.setLatLng([ac.lat, ac.lon]);
        m.setIcon(icon);
      }
    }
    // remove aircraft no longer reported
    for (const [icao, m] of this.markers) {
      if (!seen.has(icao)) {
        this.map.removeLayer(m);
        this.markers.delete(icao);
      }
    }
    // center on traffic the first time we actually see some
    if (!this.didAutoCenter && this.markers.size > 0) {
      this.didAutoCenter = true;
      const group = L.featureGroup([...this.markers.values()]);
      this.map.fitBounds(group.getBounds().pad(0.3), { maxZoom: 9 });
    }
  }
}

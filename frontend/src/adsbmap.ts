// Aircraft map for ADS-B mode (Leaflet + OpenStreetMap tiles).
//
// Plots each aircraft with a position as a heading-rotated marker labelled with
// its callsign + altitude. Stale aircraft (dropped by the backend) are removed.

import L from "leaflet";
import "leaflet/dist/leaflet.css";

export interface Aircraft {
  icao: string;
  flight?: string;
  alt?: number;
  speed?: number;
  track?: number;
  lat?: number;
  lon?: number;
  vert_rate?: number;
  squawk?: string;
  ground?: boolean;
  msgs?: number;
  age?: number;
}

export interface Vessel {
  mmsi: number;
  name?: string;
  lat?: number;
  lon?: number;
  speed?: number;
  course?: number;
  heading?: number;
  ship_type?: number;
  callsign?: string;
  dest?: string;
  age?: number;
}

function vesselPopupHtml(v: Vessel): string {
  const rows: [string, string][] = [];
  rows.push(["Name", v.name || "—"]);
  rows.push(["MMSI", String(v.mmsi)]);
  if (v.callsign) rows.push(["Callsign", v.callsign]);
  if (v.speed != null) rows.push(["Speed", `${v.speed} kn`]);
  if (v.course != null) rows.push(["Course", `${v.course}°`]);
  if (v.heading != null) rows.push(["Heading", `${v.heading}°`]);
  if (v.dest) rows.push(["Destination", v.dest]);
  if (v.age != null) rows.push(["Seen", `${v.age}s ago`]);
  const body = rows.map(([k, val]) => `<tr><td>${k}</td><td>${val}</td></tr>`).join("");
  return `<div class="ac-popup"><b>${v.name || v.mmsi}</b><table>${body}</table></div>`;
}

function popupHtml(ac: Aircraft): string {
  const rows: [string, string][] = [];
  rows.push(["Callsign", ac.flight || "—"]);
  rows.push(["ICAO", ac.icao.toUpperCase()]);
  if (ac.squawk) rows.push(["Squawk", ac.squawk]);
  if (ac.alt != null) rows.push(["Altitude", `${ac.alt.toLocaleString()} ft`]);
  if (ac.vert_rate != null) rows.push(["Climb", `${ac.vert_rate > 0 ? "+" : ""}${ac.vert_rate} ft/min`]);
  if (ac.speed != null) rows.push(["Speed", `${ac.speed} kt`]);
  if (ac.track != null) rows.push(["Track", `${ac.track}°`]);
  if (ac.ground) rows.push(["State", "on ground"]);
  if (ac.age != null) rows.push(["Seen", `${ac.age}s ago`]);
  const body = rows
    .map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`)
    .join("");
  return `<div class="ac-popup"><b>${ac.flight || ac.icao.toUpperCase()}</b><table>${body}</table></div>`;
}

export class AdsbMap {
  private map: L.Map | null = null;
  private markers = new Map<string, L.Marker>();
  private vesselMarkers = new Map<string, L.Marker>();
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
        m.bindPopup(popupHtml(ac));
        this.markers.set(ac.icao, m);
      } else {
        m.setLatLng([ac.lat, ac.lon]);
        m.setIcon(icon);
        const popup = m.getPopup();
        if (popup) {
          popup.setContent(popupHtml(ac)); // refresh details (even while open)
        }
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

  // Pan to an aircraft (from the list) and open its detail popup.
  focus(icao: string): void {
    const m = this.markers.get(icao);
    if (!m || !this.map) return;
    this.map.panTo(m.getLatLng());
    m.openPopup();
  }

  // --- vessels (AIS) -----------------------------------------------------
  updateVessels(list: Vessel[]): void {
    if (!this.map) return;
    const seen = new Set<string>();
    for (const v of list) {
      if (typeof v.lat !== "number" || typeof v.lon !== "number") continue;
      const id = String(v.mmsi);
      seen.add(id);
      const dir = v.heading ?? v.course ?? 0;
      const label = v.name || String(v.mmsi);
      const icon = L.divIcon({
        className: "ship-icon",
        html:
          `<div class="ship-rot" style="transform:rotate(${dir}deg)">▲</div>` +
          `<span class="plane-label">${label}</span>`,
        iconSize: [18, 18],
        iconAnchor: [9, 9],
      });
      let m = this.vesselMarkers.get(id);
      if (!m) {
        m = L.marker([v.lat, v.lon], { icon }).addTo(this.map);
        m.bindPopup(vesselPopupHtml(v));
        this.vesselMarkers.set(id, m);
      } else {
        m.setLatLng([v.lat, v.lon]);
        m.setIcon(icon);
        m.getPopup()?.setContent(vesselPopupHtml(v));
      }
    }
    for (const [id, m] of this.vesselMarkers) {
      if (!seen.has(id)) {
        this.map.removeLayer(m);
        this.vesselMarkers.delete(id);
      }
    }
    if (!this.didAutoCenter && this.vesselMarkers.size > 0) {
      this.didAutoCenter = true;
      const group = L.featureGroup([...this.vesselMarkers.values()]);
      this.map.fitBounds(group.getBounds().pad(0.3), { maxZoom: 11 });
    }
  }

  vesselFocus(mmsi: string): void {
    const m = this.vesselMarkers.get(mmsi);
    if (!m || !this.map) return;
    this.map.panTo(m.getLatLng());
    m.openPopup();
  }

  // Clear one layer when switching modes; reset auto-center for the new layer.
  clearAircraft(): void {
    for (const m of this.markers.values()) this.map?.removeLayer(m);
    this.markers.clear();
    this.didAutoCenter = false;
  }
  clearVessels(): void {
    for (const m of this.vesselMarkers.values()) this.map?.removeLayer(m);
    this.vesselMarkers.clear();
    this.didAutoCenter = false;
  }
}

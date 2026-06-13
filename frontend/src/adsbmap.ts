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
  type?: string;
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
  type?: string;
  callsign?: string;
  dest?: string;
  age?: number;
}

export interface Station {
  call: string;
  lat?: number;
  lon?: number;
  comment?: string;
  speed?: number;
  course?: number;
  altitude?: number;
  symbol?: string;
  kind?: string;
  age?: number;
}

function stationPopupHtml(s: Station): string {
  const rows: [string, string][] = [];
  rows.push(["Station", s.call]);
  if (s.kind) rows.push(["Type", s.kind]);
  if (s.speed != null) rows.push(["Speed", `${s.speed} km/h`]);
  if (s.course != null) rows.push(["Course", `${s.course}°`]);
  if (s.altitude != null) rows.push(["Altitude", `${Math.round(s.altitude)} m`]);
  if (s.comment) rows.push(["Comment", s.comment]);
  if (s.age != null) rows.push(["Seen", `${s.age}s ago`]);
  const body = rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");
  return `<div class="ac-popup"><b>${s.call}</b><table>${body}</table></div>`;
}

function vesselPopupHtml(v: Vessel): string {
  const rows: [string, string][] = [];
  rows.push(["Name", v.name || "—"]);
  rows.push(["MMSI", String(v.mmsi)]);
  if (v.type) rows.push(["Type", v.type]);
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
  if (ac.type) rows.push(["Type", ac.type]);
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
  private stationMarkers = new Map<string, L.Marker>();
  private aircraftTracks = new Map<string, { line: L.Polyline; pts: L.LatLngTuple[] }>();
  private vesselTracks = new Map<string, { line: L.Polyline; pts: L.LatLngTuple[] }>();
  private stationTracks = new Map<string, { line: L.Polyline; pts: L.LatLngTuple[] }>();
  private didAutoCenter = false;

  private static MAX_TRACK_PTS = 400;

  // Append a position to a target's trail polyline (creating it on first sight).
  private addTrackPoint(
    store: Map<string, { line: L.Polyline; pts: L.LatLngTuple[] }>,
    id: string,
    lat: number,
    lon: number,
    color: string,
  ): void {
    let t = store.get(id);
    if (!t) {
      t = { line: L.polyline([], { color, weight: 1.5, opacity: 0.55 }).addTo(this.map!), pts: [] };
      store.set(id, t);
    }
    const last = t.pts[t.pts.length - 1];
    if (!last || last[0] !== lat || last[1] !== lon) {
      t.pts.push([lat, lon]);
      if (t.pts.length > AdsbMap.MAX_TRACK_PTS) t.pts.shift();
      t.line.setLatLngs(t.pts);
    }
  }

  private pruneTracks(
    store: Map<string, { line: L.Polyline; pts: L.LatLngTuple[] }>,
    seen: Set<string>,
  ): void {
    for (const [id, t] of store) {
      if (!seen.has(id)) {
        this.map?.removeLayer(t.line);
        store.delete(id);
      }
    }
  }

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
      this.addTrackPoint(this.aircraftTracks, ac.icao, ac.lat, ac.lon, "#16a3c7");
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
    this.pruneTracks(this.aircraftTracks, seen);
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
      this.addTrackPoint(this.vesselTracks, id, v.lat, v.lon, "#1f9d55");
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
    this.pruneTracks(this.vesselTracks, seen);
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

  // --- APRS stations -----------------------------------------------------
  updateStations(list: Station[]): void {
    if (!this.map) return;
    const seen = new Set<string>();
    for (const s of list) {
      if (typeof s.lat !== "number" || typeof s.lon !== "number") continue;
      seen.add(s.call);
      this.addTrackPoint(this.stationTracks, s.call, s.lat, s.lon, "#e8a33d");
      const icon = L.divIcon({
        className: "station-icon",
        html:
          `<div class="station-dot"></div>` +
          `<span class="plane-label">${s.call}</span>`,
        iconSize: [12, 12],
        iconAnchor: [6, 6],
      });
      let m = this.stationMarkers.get(s.call);
      if (!m) {
        m = L.marker([s.lat, s.lon], { icon }).addTo(this.map);
        m.bindPopup(stationPopupHtml(s));
        this.stationMarkers.set(s.call, m);
      } else {
        m.setLatLng([s.lat, s.lon]);
        m.setIcon(icon);
        m.getPopup()?.setContent(stationPopupHtml(s));
      }
    }
    for (const [id, m] of this.stationMarkers) {
      if (!seen.has(id)) {
        this.map.removeLayer(m);
        this.stationMarkers.delete(id);
      }
    }
    this.pruneTracks(this.stationTracks, seen);
    if (!this.didAutoCenter && this.stationMarkers.size > 0) {
      this.didAutoCenter = true;
      const group = L.featureGroup([...this.stationMarkers.values()]);
      this.map.fitBounds(group.getBounds().pad(0.3), { maxZoom: 10 });
    }
  }

  stationFocus(call: string): void {
    const m = this.stationMarkers.get(call);
    if (!m || !this.map) return;
    this.map.panTo(m.getLatLng());
    m.openPopup();
  }

  clearStations(): void {
    for (const m of this.stationMarkers.values()) this.map?.removeLayer(m);
    for (const t of this.stationTracks.values()) this.map?.removeLayer(t.line);
    this.stationMarkers.clear();
    this.stationTracks.clear();
    this.didAutoCenter = false;
  }

  // Clear one layer when switching modes; reset auto-center for the new layer.
  clearAircraft(): void {
    for (const m of this.markers.values()) this.map?.removeLayer(m);
    for (const t of this.aircraftTracks.values()) this.map?.removeLayer(t.line);
    this.markers.clear();
    this.aircraftTracks.clear();
    this.didAutoCenter = false;
  }
  clearVessels(): void {
    for (const m of this.vesselMarkers.values()) this.map?.removeLayer(m);
    for (const t of this.vesselTracks.values()) this.map?.removeLayer(t.line);
    this.vesselMarkers.clear();
    this.vesselTracks.clear();
    this.didAutoCenter = false;
  }
}

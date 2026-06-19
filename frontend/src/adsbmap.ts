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
  turn?: number; // rate of turn, °/min
  status?: string; // navigation status label
  ship_type?: number;
  type?: string;
  callsign?: string;
  imo?: number;
  length?: number; // metres (bow→stern)
  width?: number; // metres (port→starboard beam)
  draught?: number; // metres
  dest?: string;
  eta?: string;
  country?: string; // ISO-3166 alpha-2, from the MMSI's MID
  comment?: string; // user note (persisted in the backend AIS cache)
  age?: number;
}

// ISO-3166 alpha-2 -> flag emoji (regional-indicator letters).
export function flagEmoji(iso2?: string): string {
  if (!iso2 || iso2.length !== 2) return "";
  const base = 0x1f1e6;
  return String.fromCodePoint(
    base + iso2.charCodeAt(0) - 65,
    base + iso2.charCodeAt(1) - 65,
  );
}

// ISO-3166 alpha-2 -> localized country name (falls back to the code).
export function countryName(iso2?: string): string {
  if (!iso2) return "";
  try {
    return new Intl.DisplayNames(["en"], { type: "region" }).of(iso2) || iso2;
  } catch {
    return iso2;
  }
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
  if (v.imo) rows.push(["IMO", String(v.imo)]);
  if (v.country) rows.push(["Flag", `${flagEmoji(v.country)} ${countryName(v.country)}`]);
  if (v.callsign) rows.push(["Callsign", v.callsign]);
  if (v.type) rows.push(["Type", v.type]);
  if (v.status) rows.push(["Status", v.status]);
  if (v.length != null && v.width != null)
    rows.push(["Size", `${v.length} × ${v.width} m`]);
  else if (v.length != null) rows.push(["Length", `${v.length} m`]);
  if (v.draught != null) rows.push(["Draught", `${v.draught} m`]);
  if (v.speed != null) rows.push(["Speed", `${v.speed} kn`]);
  if (v.course != null) rows.push(["Course", `${v.course}°`]);
  if (v.heading != null) rows.push(["Heading", `${v.heading}°`]);
  if (v.turn != null) rows.push(["Rate of turn", `${v.turn}°/min`]);
  if (v.dest) rows.push(["Destination", v.dest]);
  if (v.eta) rows.push(["ETA", v.eta]);
  if (v.comment) rows.push(["Note", v.comment]);
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
  private homeMarker: L.CircleMarker | null = null;
  private tracksVisible = true;
  private didAutoCenter = false;
  private followId: string | null = null;
  private followKind: "aircraft" | "vessel" | "station" | null = null;

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
      const line = L.polyline([], { color, weight: 1.5, opacity: 0.55 });
      if (this.tracksVisible) line.addTo(this.map!);
      t = { line, pts: [] };
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
    this.map.on("dragstart", () => this.clearFollow());
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
        if (this.followKind === "aircraft" && this.followId === icao)
          this.clearFollow();
      }
    }
    this.pruneTracks(this.aircraftTracks, seen);
    this.applyFollow();
    // center on traffic the first time we actually see some
    if (!this.didAutoCenter && this.markers.size > 0) {
      this.didAutoCenter = true;
      const group = L.featureGroup([...this.markers.values()]);
      this.map.fitBounds(group.getBounds().pad(0.3), { maxZoom: 9 });
    }
  }

  // --- receiver ("home") marker ------------------------------------------
  // A blue dot at the user's configured lat/lon, so they can sanity-check that
  // the position driving distance/bearing is actually where they are.
  setHome(lat: number, lon: number): void {
    if (!this.map) return;
    if (!this.homeMarker) {
      this.homeMarker = L.circleMarker([lat, lon], {
        radius: 4, color: "#fff", weight: 1.5,
        fillColor: "#2f81f7", fillOpacity: 1,
      }).addTo(this.map);
      this.homeMarker.bindPopup("Your location");
    } else {
      this.homeMarker.setLatLng([lat, lon]);
    }
    this.homeMarker.bringToFront();
  }

  clearHome(): void {
    if (this.homeMarker) {
      this.map?.removeLayer(this.homeMarker);
      this.homeMarker = null;
    }
  }

  // --- track trails on/off ------------------------------------------------
  setTracksVisible(visible: boolean): void {
    this.tracksVisible = visible;
    for (const store of [this.aircraftTracks, this.vesselTracks, this.stationTracks]) {
      for (const t of store.values()) {
        if (visible) t.line.addTo(this.map!);
        else this.map?.removeLayer(t.line);
      }
    }
  }

  // Center on an aircraft (from the list), open its popup, and follow it.
  focus(icao: string): void {
    const m = this.markers.get(icao);
    if (!m || !this.map) return;
    this.followId = icao;
    this.followKind = "aircraft";
    this.map.setView(m.getLatLng(), Math.max(this.map.getZoom(), 9));
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
      // scale the marker by vessel length so a dinghy looks small and a tanker big
      const size = v.length ? Math.max(13, Math.min(34, 11 + v.length / 6)) : 14;
      const icon = L.divIcon({
        className: "ship-icon",
        html:
          `<div class="ship-rot" style="font-size:${size}px;transform:rotate(${dir}deg)">▲</div>` +
          `<span class="plane-label">${label}</span>`,
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
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
        if (this.followKind === "vessel" && this.followId === id)
          this.clearFollow();
      }
    }
    this.pruneTracks(this.vesselTracks, seen);
    this.applyFollow();
    if (!this.didAutoCenter && this.vesselMarkers.size > 0) {
      this.didAutoCenter = true;
      const group = L.featureGroup([...this.vesselMarkers.values()]);
      this.map.fitBounds(group.getBounds().pad(0.3), { maxZoom: 11 });
    }
  }

  vesselFocus(mmsi: string): void {
    const m = this.vesselMarkers.get(mmsi);
    if (!m || !this.map) return;
    this.followId = mmsi;
    this.followKind = "vessel";
    this.map.setView(m.getLatLng(), Math.max(this.map.getZoom(), 11));
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
        if (this.followKind === "station" && this.followId === id)
          this.clearFollow();
      }
    }
    this.pruneTracks(this.stationTracks, seen);
    this.applyFollow();
    if (!this.didAutoCenter && this.stationMarkers.size > 0) {
      this.didAutoCenter = true;
      const group = L.featureGroup([...this.stationMarkers.values()]);
      this.map.fitBounds(group.getBounds().pad(0.3), { maxZoom: 10 });
    }
  }

  stationFocus(call: string): void {
    const m = this.stationMarkers.get(call);
    if (!m || !this.map) return;
    this.followId = call;
    this.followKind = "station";
    this.map.setView(m.getLatLng(), Math.max(this.map.getZoom(), 10));
    m.openPopup();
  }

  private applyFollow(): void {
    if (!this.followId || !this.map) return;
    let m: L.Marker | undefined;
    if (this.followKind === "aircraft") m = this.markers.get(this.followId);
    else if (this.followKind === "vessel") m = this.vesselMarkers.get(this.followId);
    else if (this.followKind === "station") m = this.stationMarkers.get(this.followId);
    if (m) this.map.panTo(m.getLatLng());
  }

  private clearFollow(): void {
    this.followId = null;
    this.followKind = null;
  }

  clearStations(): void {
    for (const m of this.stationMarkers.values()) this.map?.removeLayer(m);
    for (const t of this.stationTracks.values()) this.map?.removeLayer(t.line);
    this.stationMarkers.clear();
    this.stationTracks.clear();
    this.didAutoCenter = false;
    this.clearFollow();
  }

  // Clear one layer when switching modes; reset auto-center for the new layer.
  clearAircraft(): void {
    for (const m of this.markers.values()) this.map?.removeLayer(m);
    for (const t of this.aircraftTracks.values()) this.map?.removeLayer(t.line);
    this.markers.clear();
    this.aircraftTracks.clear();
    this.didAutoCenter = false;
    this.clearFollow();
  }
  clearVessels(): void {
    for (const m of this.vesselMarkers.values()) this.map?.removeLayer(m);
    for (const t of this.vesselTracks.values()) this.map?.removeLayer(t.line);
    this.vesselMarkers.clear();
    this.vesselTracks.clear();
    this.didAutoCenter = false;
    this.clearFollow();
  }
}

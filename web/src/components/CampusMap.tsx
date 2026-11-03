import { useEffect, useMemo } from "react";
import L from "leaflet";
import { MapContainer, Marker, Popup, TileLayer, useMap } from "react-leaflet";
import type { Campus, Report } from "../types";
import { timeAbsolute, timeAgo } from "../lib/format";

interface Props {
  campus: Campus;
  reports: Report[];
  selectedId: string | null;
  onSelect: (report: Report) => void;
}

interface PinGroup {
  key: string;
  name: string;
  latitude: number;
  longitude: number;
  accuracy: number | null;
  reports: Report[];
}

function FitCampusBounds({ bounds }: { bounds: [[number, number], [number, number]] }) {
  const map = useMap();

  useEffect(() => {
    map.fitBounds(bounds, { padding: [22, 22] });
  }, [bounds, map]);

  return null;
}

function FocusReport({ reportId, position }: { reportId: string | null; position: [number, number] | null }) {
  const map = useMap();

  useEffect(() => {
    if (!position) return;
    map.flyTo(position, Math.max(map.getZoom(), 17), { duration: 0.65 });
  }, [map, position, reportId]);

  return null;
}

function ReportMarker({ group, onSelect }: { group: PinGroup; onSelect: Props["onSelect"] }) {
  const kinds = new Set(group.reports.map((report) => report.kind));
  const pinKind = kinds.size > 1 ? "mixed" : group.reports[0].kind;
  const icon = L.divIcon({
    className: "lpu-report-icon",
    html: `<span class="lpu-report-count lpu-report-${pinKind}">${group.reports.length}</span>`,
    iconSize: [34, 34],
    iconAnchor: [17, 17],
    popupAnchor: [0, -17],
  });

  return (
    <Marker
      position={[group.latitude, group.longitude]}
      icon={icon}
      title={`${group.name}: ${group.reports.length} reports`}
      riseOnHover
    >
      <Popup className="lpu-report-popup" minWidth={210}>
        <div className="lpu-popup-content">
          <strong>{group.name}</strong>
          <span className="lpu-popup-count">{group.reports.length} reports</span>
          {group.accuracy != null && (
            <span className="lpu-popup-count">GPS accuracy ±{Math.round(group.accuracy)} m</span>
          )}
          {group.reports.slice(0, 6).map((report) => (
            <button key={report.id} className="lpu-popup-report" onClick={() => onSelect(report)}>
              <span className={`kind-label kind-${report.kind}`}>{report.kind}</span>
              <span>{report.description}</span>
              <small>
                {report.id} ·{" "}
                <time dateTime={report.created_at} title={timeAbsolute(report.created_at, report.campus_timezone)}>
                  {timeAgo(report.created_at)}
                </time>
              </small>
            </button>
          ))}
          {group.reports.length > 6 && (
            <span className="lpu-popup-count">And {group.reports.length - 6} more</span>
          )}
        </div>
      </Popup>
    </Marker>
  );
}

export default function CampusMap({ campus, reports, selectedId, onSelect }: Props) {
  const nodeMap = useMemo(() => new Map(campus.nodes.map((node) => [node.key, node])), [campus.nodes]);
  const pinGroups = useMemo(() => {
    const groups = new Map<string, PinGroup>();
    for (const report of reports) {
      const node = report.node_key ? nodeMap.get(report.node_key) : undefined;
      const latitude = report.latitude ?? node?.latitude;
      const longitude = report.longitude ?? node?.longitude;
      if (latitude == null || longitude == null) continue;
      const groupKey =
        report.latitude != null && report.longitude != null
          ? `gps:${latitude.toFixed(5)}:${longitude.toFixed(5)}`
          : node?.building;
      if (!groupKey) continue;
      const group = groups.get(groupKey) ?? {
        key: groupKey,
        name:
          report.latitude != null
            ? report.location || "Live location"
            : (campus.buildings.find((building) => building.key === node?.building)?.name ??
              node?.label ??
              "Campus location"),
        latitude,
        longitude,
        accuracy: report.location_accuracy_m ?? null,
        reports: [],
      };
      group.reports.push(report);
      groups.set(groupKey, group);
    }
    return [...groups.values()];
  }, [campus.buildings, nodeMap, reports]);
  const bounds = campus.map_bounds ?? [
    [31.2457534, 75.6973474],
    [31.2607949, 75.7091258],
  ];
  const center = campus.map_center ?? [31.25327, 75.70324];
  const mappedReports = pinGroups.flatMap((group) => group.reports);
  const selectedReport = reports.find((report) => report.id === selectedId);
  const selectedNode = selectedReport?.node_key ? nodeMap.get(selectedReport.node_key) : undefined;
  const selectedPosition =
    selectedReport?.latitude != null && selectedReport.longitude != null
      ? ([selectedReport.latitude, selectedReport.longitude] as [number, number])
      : selectedNode?.latitude != null && selectedNode.longitude != null
        ? ([selectedNode.latitude, selectedNode.longitude] as [number, number])
        : null;

  return (
    <div className="map-wrap">
      <MapContainer
        className="map-leaflet"
        center={center}
        zoom={15}
        minZoom={14}
        maxZoom={19}
        scrollWheelZoom
        aria-label="OpenStreetMap view of Lovely Professional University"
      >
        <FitCampusBounds bounds={bounds} />
        <FocusReport reportId={selectedId} position={selectedPosition} />
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          maxZoom={19}
        />
        {pinGroups.map((group) => (
          <ReportMarker key={group.key} group={group} onSelect={onSelect} />
        ))}
      </MapContainer>

      <div className="map-live-label">
        <span className="map-live-dot" /> LPU · PHAGWARA
      </div>
      <div className="map-legend">
        <span>
          <i className="dot dot-lost" /> lost{" "}
          {mappedReports.filter((report) => report.kind === "lost").length}
        </span>
        <span>
          <i className="dot dot-found" /> found{" "}
          {mappedReports.filter((report) => report.kind === "found").length}
        </span>
      </div>
    </div>
  );
}

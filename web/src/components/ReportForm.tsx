import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { api } from "../api";
import type { Campus, ExtractPreview, Kind, ReportDraft } from "../types";

interface Props {
  campus: Campus;
  busy: boolean;
  onSubmit: (draft: ReportDraft) => Promise<void>;
}

const WINDOWS = [
  { hours: 1, label: "in the last hour" },
  { hours: 6, label: "in the last 6 hours" },
  { hours: 24, label: "today (last 24 h)" },
  { hours: 72, label: "in the last 3 days" },
];

export default function ReportForm({ campus, busy, onSubmit }: Props) {
  const [kind, setKind] = useState<Kind>("lost");
  const [website, setWebsite] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [contactPhone, setContactPhone] = useState("");
  const formStartedAt = useRef<number | null>(null);
  const [description, setDescription] = useState("");
  const [nodeKey, setNodeKey] = useState("");
  const [windowHours, setWindowHours] = useState(24);
  const [preview, setPreview] = useState<ExtractPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [photos, setPhotos] = useState<File[]>([]);
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [liveLocation, setLiveLocation] = useState<{
    latitude: number;
    longitude: number;
    accuracy: number;
  } | null>(null);
  const [locationError, setLocationError] = useState<string | null>(null);
  const [locating, setLocating] = useState(false);

  useEffect(() => {
    formStartedAt.current = Date.now() / 1000;
  }, []);

  const primaryPhoto = photos[0] ?? null;
  useEffect(() => {
    if (!primaryPhoto) {
      setPhotoUrl(null);
      return;
    }
    const url = URL.createObjectURL(primaryPhoto);
    setPhotoUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [primaryPhoto]);

  useEffect(() => {
    let alive = true;
    const text = description.trim();
    if (text.length < 4) {
      setPreview(null);
      setPreviewError(null);
      return;
    }
    setPreviewError(null);
    const timer = setTimeout(() => {
      api
        .extract(text)
        .then((p) => {
          if (alive) setPreview(p);
        })
        .catch((err: unknown) => {
          if (!alive) return;
          setPreview(null);
          setPreviewError(err instanceof Error ? err.message : "Attribute preview is unavailable.");
        });
    }, 320);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [description]);

  const groups = useMemo(() => {
    const byBuilding = new Map<string, { key: string; label: string; floor: number | null }[]>();
    for (const n of campus.nodes) {
      const list = byBuilding.get(n.building) ?? [];
      list.push({ key: n.key, label: n.label, floor: n.floor });
      byBuilding.set(n.building, list);
    }
    return campus.buildings
      .filter((building) => building.latitude != null && building.longitude != null)
      .map((b) => ({
        building: b,
        nodes: (byBuilding.get(b.key) ?? []).sort((a, z) => (a.floor ?? 0) - (z.floor ?? 0)),
      }))
      .filter((g) => g.nodes.length > 0);
  }, [campus]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (description.trim().length < 4 && photos.length === 0) {
      setError("Say what the item is, or attach a photo instead.");
      return;
    }
    if (!contactEmail.trim() && !contactPhone.trim()) {
      setError("Add an email address or phone number so the desk can contact you.");
      return;
    }
    const now = new Date();
    const from = new Date(now.getTime() - windowHours * 3600_000);
    try {
      await onSubmit({
        kind,
        description: description.trim(),
        node_key: nodeKey || null,
        latitude: liveLocation?.latitude ?? null,
        longitude: liveLocation?.longitude ?? null,
        location_accuracy_m: liveLocation?.accuracy ?? null,
        lost_from: kind === "lost" ? from.toISOString() : null,
        lost_to: kind === "lost" ? now.toISOString() : null,
        contact_email: contactEmail.trim(),
        contact_phone: contactPhone.trim(),
        website,
        form_started_at: formStartedAt.current ?? Date.now() / 1000,
        photos,
      });
      setDescription("");
      setWebsite("");
      formStartedAt.current = Date.now() / 1000;
      setPreview(null);
      setPhotos([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not file this report.");
    }
  }

  function captureLocation() {
    setLocationError(null);
    if (!navigator.geolocation) {
      setLocationError("Location is not available in this browser.");
      return;
    }

    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        setNodeKey("");
        setLiveLocation({
          latitude: coords.latitude,
          longitude: coords.longitude,
          accuracy: coords.accuracy,
        });
        setLocating(false);
      },
      (geoError) => {
        setLocationError(
          geoError.code === geoError.PERMISSION_DENIED
            ? "Location permission was denied. You can still choose a building above."
            : "Could not get your location. Try again or choose a building above.",
        );
        setLocating(false);
      },
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 30000 },
    );
  }

  function saveDraft() {
    window.localStorage.setItem(
      "findly.report.draft",
      JSON.stringify({ kind, description, nodeKey, windowHours }),
    );
    setError("Draft saved on this device.");
  }

  const attrs = preview?.attrs;
  const rows: { label: string; value: string; swatch?: string }[] = [];
  if (attrs) {
    if (attrs.category) rows.push({ label: "category", value: attrs.category });
    if (attrs.brand) rows.push({ label: "brand", value: attrs.brand });
    if (attrs.model) rows.push({ label: "model", value: attrs.model });
    if (attrs.primary_color)
      rows.push({
        label: "colour",
        value: attrs.primary_color,
        swatch: preview?.primary_color_hex ?? undefined,
      });
    if (attrs.secondary_color) rows.push({ label: "second colour", value: attrs.secondary_color });
    if (attrs.material) rows.push({ label: "material", value: attrs.material });
    if (attrs.damages?.length) rows.push({ label: "damage", value: attrs.damages.join(", ") });
    if (attrs.marks?.length) rows.push({ label: "mark", value: attrs.marks.join(", ") });
    if (attrs.visible_text?.length)
      rows.push({ label: "printed text", value: attrs.visible_text.join(", ") });
  }

  return (
    <form className="form" onSubmit={submit}>
      <label className="honeypot-field" aria-hidden="true">
        Leave this field empty
        <input
          name="website"
          value={website}
          onChange={(e) => setWebsite(e.target.value)}
          tabIndex={-1}
          autoComplete="off"
        />
      </label>
      <div className="report-kind-cards" role="tablist" aria-label="Report kind">
        {(["lost", "found"] as const).map((k) => (
          <button
            key={k}
            type="button"
            role="tab"
            aria-selected={kind === k}
            className={kind === k ? "is-active" : ""}
            onClick={() => setKind(k)}
          >
            <span className="report-kind-icon" aria-hidden="true">{k === "lost" ? "−" : "+"}</span>
            <span>
              <strong>{k === "lost" ? "I lost something" : "I found something"}</strong>
              <small>{k === "lost" ? "Tell us what went missing" : "Help return an item to its owner"}</small>
            </span>
          </button>
        ))}
      </div>

      <label className="field">
        <span className="field-label field-label-row">
          <span>Tell us about the item</span>
          <small>{description.length}/1000</small>
        </span>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={4}
          maxLength={1000}
          placeholder={
            kind === "lost"
              ? "e.g. Lost my black Milton steel water bottle near the canteen, has a blue tape on the lid."
              : "e.g. Found a grey Wildcraft backpack in the library, 3rd floor, torn strap."
          }
        />
      </label>

      <div className="report-safety-banner">
        <span aria-hidden="true">!</span>
        <p><strong>Keep it safe</strong> Never include passwords, access codes, or other sensitive information in your report.</p>
      </div>

      <div className="field-row">
        <label className="field">
          <span className="field-label">Contact email or phone</span>
          <input
            className="search"
            type="email"
            value={contactEmail}
            onChange={(e) => setContactEmail(e.target.value)}
            maxLength={254}
            autoComplete="email"
          />
        </label>
        <label className="field">
          <span className="field-label">Phone (international)</span>
          <input
            className="search"
            type="tel"
            value={contactPhone}
            onChange={(e) => setContactPhone(e.target.value)}
            maxLength={20}
            placeholder="+911234567890"
            autoComplete="tel"
          />
        </label>
      </div>

      <div className="field">
        <span className="field-label">Photos (optional, up to 4)</span>
        {photos.length > 0 ? (
          <div className="photo-chip">
            {photoUrl && <img src={photoUrl} alt="Selected item" />}
            <div className="photo-chip-body">
              <strong>
                {photos.length === 1
                  ? (photos[0]?.name ?? "1 photo")
                  : `${photos.length} photos selected`}
              </strong>
              <span>
                {photos.length === 1
                  ? `${Math.max(1, Math.round((photos[0]?.size ?? 0) / 1024))} KB · colour and shape are read from the photo`
                  : "Colour and shape are read from every photo"}
              </span>
            </div>
            <button
              type="button"
              className="photo-remove"
              onClick={() => setPhotos([])}
              aria-label="Remove photos"
            >
              ✕
            </button>
          </div>
        ) : (
          <label className="photo-drop">
            <input
              type="file"
              accept="image/*"
              multiple
              onChange={(e: ChangeEvent<HTMLInputElement>) =>
                setPhotos(Array.from(e.target.files ?? []).slice(0, 4))
              }
            />
            <span className="photo-drop-plus" aria-hidden="true">
              ＋
            </span>
            <span>
              <strong>Attach photos</strong>
              <em>JPEG or PNG, up to 8 MB each. Add up to 4. EXIF metadata is stripped on upload.</em>
            </span>
          </label>
        )}
      </div>

      <div className="field-row">
        <label className="field">
          <span className="field-label">Where?</span>
          <select value={nodeKey} onChange={(e) => setNodeKey(e.target.value)}>
            <option value="">Not sure / skip</option>
            {groups.map((g) => (
              <optgroup key={g.building.key} label={g.building.name}>
                {g.nodes.map((n) => (
                  <option key={n.key} value={n.key}>
                    {n.floor === null
                      ? g.building.name
                      : g.building.floors === 1
                        ? "Whole building"
                        : `Floor ${n.floor}`}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
          <div className="live-location">
              {liveLocation ? (
                <div className="live-location-active">
                  <span className="live-location-indicator" aria-hidden="true" />
                  <span>
                    Location added · ±{Math.round(liveLocation.accuracy)} m
                    <small>
                      {liveLocation.latitude.toFixed(5)}, {liveLocation.longitude.toFixed(5)}
                    </small>
                  </span>
                  <button
                    type="button"
                    className="live-location-remove"
                    onClick={() => setLiveLocation(null)}
                    aria-label="Remove live location"
                  >
                    ×
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  className="live-location-button"
                  onClick={captureLocation}
                  disabled={locating}
                >
                  <span aria-hidden="true">⌖</span>
                  {locating
                    ? "Getting location…"
                    : kind === "found"
                      ? "Use my current location"
                      : "Add where I last had it"}
                </button>
              )}
              <small className="location-hint">
                {kind === "found"
                  ? "Adds a precise pin to this found report."
                  : "Adds a pin where you last had the item, so distance can be compared."}
              </small>
              {locationError && (
                <p className="location-error" role="status">
                  {locationError}
                </p>
              )}
          </div>
        </label>

        {kind === "lost" && (
          <label className="field">
            <span className="field-label">Last had it</span>
            <select value={windowHours} onChange={(e) => setWindowHours(Number(e.target.value))}>
              {WINDOWS.map((w) => (
                <option key={w.hours} value={w.hours}>
                  {w.label}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      <div className="preview">
        <div className="preview-head">
          <span className="field-label">What we read from that</span>
          {preview && (
            <span className="preview-score mono">{Math.round(preview.completeness * 100)}% complete</span>
          )}
        </div>
        {previewError && (
          <p className="preview-hint" role="status">
            {previewError}
          </p>
        )}
        {rows.length === 0 ? (
          <p className="preview-empty">
            {photos.length > 0 && description.trim().length < 4
              ? "No description — the colour is read from your photos when you file."
              : description.trim().length < 4
                ? "Type a description — attributes appear here as you go."
                : "Nothing recognised yet. Naming the item type and colour helps most."}
          </p>
        ) : (
          <ul className="attr-list">
            {rows.map((r) => (
              <li key={r.label}>
                <span className="attr-label">{r.label}</span>
                <span className="attr-value">
                  {r.swatch && <i className="swatch" style={{ background: r.swatch }} />}
                  {r.value}
                </span>
              </li>
            ))}
          </ul>
        )}
        {preview && !preview.attrs.category && (
          <p className="preview-hint">
            No category recognised — mention what kind of object it is (bottle, bag, phone, ID card…).
          </p>
        )}
      </div>

      {error && <p className="form-error">{error}</p>}

      <div className="report-actions">
        <button className="report-draft-button" type="button" onClick={saveDraft}>
          Save draft
        </button>
        <button className="primary" type="submit" disabled={busy}>
        {busy ? "Filing and matching…" : "File report and find matches"}
        </button>
      </div>
      <p className="form-foot">
        Matching uses the description, the location, the time window and — if you attach one — the photo. A
        photo also supplies the colour when your description leaves it out, so you can file with just a photo.
      </p>
    </form>
  );
}

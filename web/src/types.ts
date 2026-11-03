export type Kind = "lost" | "found";
export type Decision = "match" | "review" | "no_match";
export type FeedbackAction = "confirm" | "reject";

export interface CampusNode {
  key: string;
  building: string;
  floor: number | null;
  label: string;
  x: number;
  y: number;
  outdoor: boolean;
  latitude?: number | null;
  longitude?: number | null;
}

export interface Building {
  key: string;
  name: string;
  short: string;
  kind: string;
  x: number;
  y: number;
  floors: number;
  width: number;
  height: number;
  latitude?: number | null;
  longitude?: number | null;
}

export interface Campus {
  buildings: Building[];
  nodes: CampusNode[];
  edges: { a: string; b: string; minutes: number }[];
  map_center?: [number, number] | null;
  map_bounds?: [[number, number], [number, number]] | null;
}

export interface Attrs {
  category?: string | null;
  brand?: string | null;
  model?: string | null;
  primary_color?: string | null;
  secondary_color?: string | null;
  material?: string | null;
  damages?: string[];
  marks?: string[];
  visible_text?: string[];
  confidence?: Record<string, number>;
}

export interface Report {
  id: string;
  kind: Kind;
  status: string;
  created_at: string;
  campus_timezone: string;
  description: string;
  category: string | null;
  location: string;
  node_key: string | null;
  latitude?: number | null;
  longitude?: number | null;
  location_accuracy_m?: number | null;
  lost_from: string | null;
  lost_to: string | null;
  image_path: string | null;
  image_url: string | null;
  image_urls?: string[];
  photo_count?: number;
  has_photo: boolean;
  is_synthetic_art: boolean;
  attrs: Attrs;
}

export interface ReportPage {
  count: number;
  reports: Report[];
  next_cursor: string | null;
}

export interface Reason {
  label: string;
  kind: "positive" | "negative" | "neutral";
  weight: number;
}

export interface Candidate {
  candidate_id: string;
  kind: Kind;
  prob: number;
  score: number;
  flags: string[];
  reasons: Reason[];
  feats: Record<string, number | null>;
  description: string;
  category: string | null;
  location: string;
  image_path: string | null;
  image_url?: string | null;
  created_at: string;
  campus_timezone: string;
}

export interface QueryReport {
  id: string;
  kind: Kind;
  description: string;
  category: string | null;
  location: string;
  image_path: string | null;
  image_url?: string | null;
  created_at: string;
  campus_timezone: string;
  attrs: Attrs;
}

export interface ReportDraft {
  kind: Kind;
  description: string;
  node_key: string | null;
  latitude: number | null;
  longitude: number | null;
  location_accuracy_m: number | null;
  lost_from: string | null;
  lost_to: string | null;
  contact_email: string;
  contact_phone: string;
  website: string;
  form_started_at: number;
  photos: File[];
}

export interface MatchResult {
  query: QueryReport;
  decision: Decision;
  confidence: number;
  margin: number;
  reason: string;
  feedback: Record<string, FeedbackAction>;
  matches: Candidate[];
}

export interface Stats {
  reports: number;
  lost: number;
  found: number;
  pairs: number;
  matched: number;
  confirmed: number;
  rejected: number;
  by_category: Record<string, number>;
}

export interface FeedbackItem {
  query_id: string;
  candidate_id: string;
  action: FeedbackAction;
  staff: string | null;
  note: string | null;
  prob: number | null;
  score: number | null;
  created_at: string;
}

export interface FeedbackPage {
  counts: { confirmed: number; rejected: number };
  count: number;
  items: FeedbackItem[];
  next_cursor: string | null;
}

export interface PublicStats {
  reports: number;
  lost: number;
  found: number;
  pairs: number;
}

export interface OperatingPoint {
  plausible_floor: number;
  prob_alert: number;
  margin_min: number;
  bands: {
    strong: number;
    probable: number;
    weak: number;
  };
  models: { text: string; image: string };
}

export interface AuthUser {
  id: string;
  email: string | null;
  phone_e164: string | null;
  role: string;
  campus_id: string | null;
}

export interface ManagedUser {
  id: string;
  email: string | null;
  phone_e164: string | null;
  role: string;
  campus_id: string | null;
}

export interface AuthSession {
  access_token: string;
  refresh_token: string;
  user: AuthUser;
}

export interface ChallengeQuestion {
  key: string;
  prompt: string;
}

export interface ClaimChallenge {
  lost_id: string;
  min_questions: number;
  questions: ChallengeQuestion[];
  verifiable_online: boolean;
}

export interface ClaimResult {
  claim_id: string;
  claim_token: string;
  status: "pending" | "verified" | "collected" | "rejected" | "expired";
  needs_staff: boolean;
  handover_code?: string;
  note?: string;
  reason?: string;
}

export interface ClaimSummary {
  claim_id: string;
  status: "pending" | "verified" | "collected" | "rejected" | "expired";
  query_id: string;
  candidate_id: string;
  created_at: string;
  verified_at: string | null;
  collected_at: string | null;
}

export interface ExtractPreview {
  attrs: Attrs;
  category: string | null;
  completeness: number;
  primary_color_hex: string | null;
}

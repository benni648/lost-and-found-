// Role -> dashboard visibility. The API enforces the same split server-side
// (lostfound/deps.py), so hiding a dashboard in the UI is never the only
// control: this is the presentation rule only.
import type { AuthUser } from "../types";

export type Role = "student" | "staff" | "campus_admin" | "superadmin";
export type DashboardId = "user" | "staff" | "admin";
export type View = "workspace" | DashboardId;

export const DASHBOARD_LABEL: Record<DashboardId, string> = {
  user: "My dashboard",
  staff: "Staff dashboard",
  admin: "Admin dashboard",
};

const ROLE_LABEL: Record<Role, string> = {
  student: "student",
  staff: "staff",
  campus_admin: "campus admin",
  superadmin: "super admin",
};

const VISIBLE_DASHBOARDS: Record<Role, DashboardId[]> = {
  student: ["user"],
  staff: ["user", "staff"],
  campus_admin: ["user", "admin"],
  superadmin: ["user", "admin"],
};

/** Unknown or missing roles fall back to the least-privileged view. */
export function roleOf(user: AuthUser | null): Role {
  const role = user?.role;
  return role === "staff" || role === "campus_admin" || role === "superadmin" ? role : "student";
}

export function roleLabel(role: Role): string {
  return ROLE_LABEL[role];
}

export function dashboardsFor(role: Role): DashboardId[] {
  return VISIBLE_DASHBOARDS[role];
}

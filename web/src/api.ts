/** Thin client for the ansurance API: cookie session, CSRF header on writes, typed errors. */

export interface Me {
  id: string;
  email: string;
  name: string;
  roles: string[];
  capabilities: string[];
  csrf: string;
}

export interface InboxItem {
  id: string;
  type: string;
  role: string;
  title: string;
  detail: string;
  urgency: "urgent" | "important" | "normal";
  status: string;
  client_id: string | null;
  area: string | null;
  created_at: string;
}

export interface InboxPage {
  items: InboxItem[];
  next: string | null;
  read_only: boolean;
}

export interface Counts {
  open: number;
  urgent: number;
  done_today: number;
}

export interface ClientRow {
  id: string;
  id_number: string;
  name: string;
  name_translit_he: string;
  name_translit_lat: string;
  preferred_language: string;
  status: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
  ) {
    super(`${status} ${code}`);
  }
}

export class Api {
  csrf = "";
  constructor(private fetcher: typeof fetch = (...a) => fetch(...a)) {}

  private async call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = {};
    if (body !== undefined) headers["content-type"] = "application/json";
    if (method !== "GET" && this.csrf) headers["x-csrf-token"] = this.csrf;
    const r = await this.fetcher(path, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!r.ok) {
      let code = r.statusText;
      try {
        const j = (await r.json()) as { detail?: unknown };
        if (typeof j.detail === "string") code = j.detail;
      } catch {
        /* not JSON */
      }
      throw new ApiError(r.status, code);
    }
    return (await r.json()) as T;
  }

  async login(email: string, password: string, code: string): Promise<Me> {
    const r = await this.call<{ csrf: string }>("POST", "/api/auth/login", { email, password, code });
    this.csrf = r.csrf;
    return this.me();
  }

  async me(): Promise<Me> {
    const m = await this.call<Me>("GET", "/api/auth/me");
    this.csrf = m.csrf;
    return m;
  }

  logout = () => this.call<unknown>("POST", "/api/auth/logout");
  counts = () => this.call<Counts>("GET", "/api/inbox/counts");
  inbox = (params: { status?: string; urgency?: string; cursor?: string | null }) => {
    const q = new URLSearchParams();
    if (params.status) q.set("status", params.status);
    if (params.urgency) q.set("urgency", params.urgency);
    if (params.cursor) q.set("cursor", params.cursor);
    return this.call<InboxPage>("GET", `/api/inbox?${q.toString()}`);
  };
  done = (id: string, note = "") => this.call<InboxItem>("POST", `/api/inbox/${id}/done`, { note });
  dismiss = (id: string, reason: string) => this.call<InboxItem>("POST", `/api/inbox/${id}/dismiss`, { reason });
  reassign = (id: string, role: string, note: string) =>
    this.call<InboxItem>("POST", `/api/inbox/${id}/reassign`, { role, note });
  clients = (q: string) => this.call<ClientRow[]>("GET", `/api/clients?q=${encodeURIComponent(q)}`);
}

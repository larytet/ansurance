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

export interface ClientDetail extends ClientRow {
  phone: string | null;
  email: string | null;
  family: { head: string; member: string; relation: string }[];
}

export interface PolicyRow {
  id: string;
  insurer: string;
  policy_number: string;
  product: string;
  premium_ils: number;
  renewal_date: string | null;
}

export interface DocumentRow {
  id: string;
  doc_type: string;
  filename: string;
  media_type: string;
  size: number;
  restricted: boolean;
  scan_status: string;
  channel: string;
  received_at: string;
  retention_until: string;
  legal_hold: boolean;
}

export interface ConsentState {
  current: Record<string, "opt_in" | "opt_out">;
  history: { channel: string; state: string; source: string; recorded_at: string; late: boolean }[];
}

export interface ProcessSummary {
  id: string;
  key: string;
  title: string;
  status: string;
  stages: { id: string; status: string; role: string; name: { en: string; he: string } }[];
}

export interface ProcessDef {
  key: string;
  name: { en: string; he: string };
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
  client = (id: string) => this.call<ClientDetail>("GET", `/api/clients/${id}`);
  policies = (id: string) => this.call<PolicyRow[]>("GET", `/api/clients/${id}/policies`);
  documents = (id: string) => this.call<DocumentRow[]>("GET", `/api/clients/${id}/documents`);
  consent = (id: string) => this.call<ConsentState>("GET", `/api/clients/${id}/consent`);
  recordConsent = (id: string, channel: string, state: "opt_in" | "opt_out", source: string) =>
    this.call<unknown>("POST", `/api/clients/${id}/consent`, { channel, state, source });
  processesFor = (id: string) => this.call<ProcessSummary[]>("GET", `/api/processes?client_id=${id}`);
  processDefs = () => this.call<ProcessDef[]>("GET", "/api/process-defs");
  startProcess = (key: string, clientId: string) =>
    this.call<unknown>("POST", "/api/processes", { key, client_id: clientId });
  async upload(id: string, file: File, docType: string): Promise<DocumentRow> {
    const form = new FormData();
    form.append("file", file);
    form.append("doc_type", docType);
    const r = await this.fetcher(`/api/clients/${id}/documents`, {
      method: "POST",
      headers: this.csrf ? { "x-csrf-token": this.csrf } : {},
      credentials: "same-origin",
      body: form,
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
    return (await r.json()) as DocumentRow;
  }
  clients = (q: string) => this.call<ClientRow[]>("GET", `/api/clients?q=${encodeURIComponent(q)}`);
}

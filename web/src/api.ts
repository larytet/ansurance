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

export interface Overview {
  inbox_urgent: number;
  renewals_overdue: number;
  renewals_next_30: number;
  claims_open: number;
  intake_waiting_review: number;
  kyc_waiting_approval: number;
  campaigns_sending: number;
  clients: number;
  complaints_overdue: number;
  licences_attention: number;
}

export interface AuditVerify {
  ok: boolean;
  entries: number;
  seals: number;
  sealed_entries: number;
  problems: string[];
}

export interface Campaign {
  id: string;
  name: string;
  type: string;
  purpose: string;
  status: string;
  benefit: string;
  body: string;
  base_language: string;
  requires_approval: boolean;
  approved: boolean;
}

export interface CampaignPreview {
  matching: number;
  selected: number;
  excluded: Record<string, number>;
  languages: Record<string, number>;
  sample: { name: string; language: string; channel: string }[];
}

export interface CampaignIn {
  name: string;
  type: string;
  benefit: string;
  body: string;
  base_language: string;
  segment: { has_product?: string[]; lacks_product?: string[] };
}

export interface WorkItem {
  instance_id: string;
  key: string;
  title: string;
  stage: string;
  stage_status: string;
  role: string;
  client_id: string | null;
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
        else if (j.detail && typeof j.detail === "object" && "error" in j.detail) {
          const d = j.detail as { error: string; detail?: unknown };
          code = d.detail ? `${d.error}: ${JSON.stringify(d.detail)}` : d.error;
        }
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
  overview = () => this.call<Overview>("GET", "/api/overview");
  verifyAudit = () => this.call<AuditVerify>("GET", "/api/audit/verify");
  campaigns = () => this.call<Campaign[]>("GET", "/api/campaigns");
  createCampaign = (b: CampaignIn) => this.call<Campaign>("POST", "/api/campaigns", b);
  previewCampaign = (id: string) => this.call<CampaignPreview>("GET", `/api/campaigns/${id}/preview`);
  approveCampaign = (id: string) => this.call<Campaign>("POST", `/api/campaigns/${id}/approve`);
  startCampaign = (id: string) => this.call<{ selected: number; excluded: number }>("POST", `/api/campaigns/${id}/start`);
  myWork = () => this.call<{ count: number; items: WorkItem[] }>("GET", "/api/my-work");
}

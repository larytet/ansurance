import { render } from "@testing-library/react";
import { Api } from "./api";
import { App } from "./App";
import { UiProvider } from "./ui";

export type Handler = (method: string, path: string, body: unknown) => { status?: number; json?: unknown } | undefined;

/** A fake server: handlers are tried in order; unmatched requests are 404. Records every call. */
export function fakeServer(...handlers: Handler[]) {
  const calls: { method: string; path: string; body: unknown; csrf?: string }[] = [];
  const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    let body: unknown;
    if (init?.body instanceof FormData) {
      const f = init.body.get("file");
      body = { file: f instanceof File ? f.name : null, doc_type: init.body.get("doc_type") };
    } else if (init?.body) {
      body = JSON.parse(String(init.body));
    }
    const csrf = (init?.headers as Record<string, string> | undefined)?.["x-csrf-token"];
    calls.push({ method, path, body, csrf });
    for (const h of handlers) {
      const r = h(method, path, body);
      if (r) return new Response(JSON.stringify(r.json ?? {}), { status: r.status ?? 200 });
    }
    return new Response(JSON.stringify({ detail: "not_found" }), { status: 404 });
  }) as typeof fetch;
  return { fetcher, calls };
}

export const ME = (roles: string[], caps: string[] = ["client.read"]) => ({
  id: "u1", email: "ann@agency.il", name: "Ann", roles, capabilities: caps, auth_mode: "local" as const, csrf: "tok",
});

export function mount(fetcher: typeof fetch) {
  const api = new Api(fetcher);
  return { api, ...render(<UiProvider api={api}><App /></UiProvider>) };
}

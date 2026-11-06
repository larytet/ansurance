import { useEffect, useState } from "react";
import { t, dirOf, type Lang } from "../i18n";

interface Summary {
  name: string; language: string;
  policies: { insurer: string; product: string; policy_number: string; status: string; renewal_date: string | null }[];
  documents: { type: string; filename: string; received_at: string }[];
  claims: { status: string; event_date: string; insurer_ref: string }[];
}

/** The client's read-only page. The token is in the URL fragment so it never reaches a server log. */
export function Portal({ fetcher = (...a: Parameters<typeof fetch>) => fetch(...a) }: { fetcher?: typeof fetch }) {
  const [data, setData] = useState<Summary | null>(null);
  const [bad, setBad] = useState(false);
  const token = window.location.hash.replace(/^#/, "");
  useEffect(() => {
    fetcher("/api/portal/summary", { headers: { "x-portal-token": token } })
      .then(async (r) => { if (!r.ok) throw new Error(String(r.status)); setData((await r.json()) as Summary); })
      .catch(() => setBad(true));
  }, [fetcher, token]);
  const lang: Lang = data?.language === "en" ? "en" : "he";
  return (
    <main dir={dirOf(lang)} lang={lang}>
      {bad && <p role="alert">{t(lang, "portalInvalid")}</p>}
      {data && (
        <>
          <h1>{t(lang, "portalTitle")}: <bdi>{data.name}</bdi></h1>
          <h2>{t(lang, "policies")}</h2>
          <ul>{data.policies.map((p) => <li key={p.policy_number}>{p.insurer} · {p.product} · {p.policy_number}{p.renewal_date && ` · ${t(lang, "portalRenewal")} ${p.renewal_date}`}</li>)}</ul>
          <h2>{t(lang, "documents")}</h2>
          <ul>{data.documents.map((d) => <li key={d.filename + d.received_at}>{d.type} · {d.filename}</li>)}</ul>
          <h2>{t(lang, "portalClaims")}</h2>
          <ul>{data.claims.map((c) => <li key={c.event_date + c.insurer_ref}>{c.event_date} · {c.status} {c.insurer_ref}</li>)}</ul>
        </>
      )}
    </main>
  );
}

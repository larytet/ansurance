import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  type ClientDetail, type ConsentState, type DocumentRow, type PolicyRow, type ProcessDef, type ProcessSummary,
} from "../api";
import { type Key } from "../i18n";
import { useUi } from "../ui";

const CHANNELS = ["whatsapp", "email", "sms"] as const;
const DOC_TYPES = [
  "id_card", "id_addendum", "driving_licence", "policy", "application", "claim_form", "medical_report",
  "invoice", "bank_form", "power_of_attorney", "insurer_letter", "complaint", "other",
];

export function ClientFile({ clientId, onBack }: { clientId: string; onBack: () => void }) {
  const { api, tr, lang, me } = useUi();
  const [client, setClient] = useState<ClientDetail | null>(null);
  const [policies, setPolicies] = useState<PolicyRow[] | null>(null);
  const [docs, setDocs] = useState<DocumentRow[] | null>(null);
  const [consent, setConsent] = useState<ConsentState | null>(null);
  const [procs, setProcs] = useState<ProcessSummary[] | null>(null);
  const [defs, setDefs] = useState<ProcessDef[]>([]);
  const [error, setError] = useState(false);
  const [docType, setDocType] = useState("other");
  const [source, setSource] = useState("");
  const [startKey, setStartKey] = useState("");

  const can = (c: string) => !!me?.capabilities.includes(c);

  const load = useCallback(async () => {
    setError(false);
    try {
      setClient(await api.client(clientId));
      const [p, d, c, pr, df] = await Promise.all([
        can("policy.read") ? api.policies(clientId) : Promise.resolve([]),
        can("document.read") ? api.documents(clientId) : Promise.resolve([]),
        can("consent.read") ? api.consent(clientId) : Promise.resolve(null),
        can("process.read") ? api.processesFor(clientId) : Promise.resolve([]),
        can("process.start") ? api.processDefs() : Promise.resolve([]),
      ]);
      setPolicies(p);
      setDocs(d);
      setConsent(c);
      setProcs(pr);
      setDefs(df);
      setStartKey((k) => k || df[0]?.key || "");
    } catch {
      setError(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, clientId, me]);

  useEffect(() => {
    void load();
  }, [load]);

  async function upload(e: FormEvent<HTMLInputElement>) {
    const file = e.currentTarget.files?.[0];
    if (!file) return;
    try {
      await api.upload(clientId, file, docType);
      setDocs(await api.documents(clientId));
    } catch {
      setError(true);
    }
  }

  async function record(channel: string, state: "opt_in" | "opt_out") {
    if (source.trim().length < 2) return; // the server requires a source
    try {
      await api.recordConsent(clientId, channel, state, source.trim());
      setConsent(await api.consent(clientId));
    } catch {
      setError(true);
    }
  }

  if (error && !client) return <p role="alert">{tr("error")}</p>;
  if (!client) return <p>{tr("loading")}</p>;
  const translit = lang === "he" ? client.name_translit_he : client.name_translit_lat;

  return (
    <section aria-label={client.name}>
      <button onClick={onBack}>{tr("back")}</button>
      <h2>
        <bdi>{client.name}</bdi>
        {translit && translit !== client.name && <small> <bdi>{translit}</bdi></small>}
      </h2>
      {error && <p role="alert">{tr("error")}</p>}

      <h3>{tr("details")}</h3>
      <dl className="facts">
        <dt>{tr("idNumber")}</dt><dd>{client.id_number}</dd>
        <dt>{tr("status")}</dt><dd>{client.status}</dd>
        {client.phone && (<><dt>{tr("phone")}</dt><dd><bdi>{client.phone}</bdi></dd></>)}
        {client.email && (<><dt>{tr("mail")}</dt><dd><bdi>{client.email}</bdi></dd></>)}
        {client.family.length > 0 && (<><dt>{tr("family")}</dt><dd>{client.family.map((f) => f.relation).join(", ")}</dd></>)}
      </dl>

      {can("policy.read") && (
        <>
          <h3>{tr("policies")}</h3>
          {policies && policies.length === 0 && <p>{tr("noPolicies")}</p>}
          <ul className="rows">
            {(policies ?? []).map((p) => (
              <li key={p.id}>
                <strong>{p.insurer}</strong> · <bdi>{p.policy_number}</bdi> · {p.product} · {tr("premium")} ₪{p.premium_ils}
                {p.renewal_date && <> · {tr("renewal")} {p.renewal_date}</>}
              </li>
            ))}
          </ul>
        </>
      )}

      {can("document.read") && (
        <>
          <h3>{tr("documents")}</h3>
          {docs && docs.length === 0 && <p>{tr("noDocuments")}</p>}
          <ul className="rows">
            {(docs ?? []).map((d) => (
              <li key={d.id} data-scan={d.scan_status}>
                <bdi>{d.filename}</bdi> · {d.doc_type}
                {d.restricted && <span className="badge"> {tr("restricted")}</span>}
                {d.legal_hold && <span className="badge"> {tr("legalHold")}</span>}
                <span className={`scan ${d.scan_status}`}> {tr(`scan_${d.scan_status}` as Key)}</span>
                {d.scan_status === "clean" && <> · <a href={`/api/documents/${d.id}/content`}>{tr("download")}</a></>}
              </li>
            ))}
          </ul>
          {can("document.write") && (
            <div className="upload">
              <label>{tr("docType")}{" "}
                <select value={docType} onChange={(e) => setDocType(e.target.value)}>
                  {DOC_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>{" "}
              <label>{tr("upload")}{" "}
                <input type="file" aria-label={tr("choose")} onChange={(e) => void upload(e)} />
              </label>
            </div>
          )}
        </>
      )}

      {consent && (
        <>
          <h3>{tr("consent")}</h3>
          <ul className="rows">
            {CHANNELS.map((ch) => (
              <li key={ch} data-channel={ch}>
                <strong>{tr(`channel.${ch}` as Key)}</strong>:{" "}
                {consent.current[ch] ? tr(`state.${consent.current[ch]}` as Key) : tr("never")}
                {can("consent.write") && (
                  <>
                    {" "}
                    <button onClick={() => void record(ch, "opt_in")}>{tr("optIn")}</button>{" "}
                    <button onClick={() => void record(ch, "opt_out")}>{tr("optOut")}</button>
                  </>
                )}
              </li>
            ))}
          </ul>
          {can("consent.write") && (
            <input aria-label={tr("source")} placeholder={tr("source")} value={source} onChange={(e) => setSource(e.target.value)} />
          )}
        </>
      )}

      {procs && (
        <>
          <h3>{tr("processes")}</h3>
          {procs.length === 0 && <p>{tr("noProcesses")}</p>}
          <ul className="rows">
            {procs.map((p) => (
              <li key={p.id} data-process={p.key}>
                <strong>{p.title}</strong>
                <ol className="pipeline">
                  {p.stages.map((s) => (
                    <li key={s.id} className={`stage ${s.status}`} title={s.status}>{s.name[lang]}</li>
                  ))}
                </ol>
              </li>
            ))}
          </ul>
          {defs.length > 0 && (
            <div>
              <select aria-label={tr("startProcess")} value={startKey} onChange={(e) => setStartKey(e.target.value)}>
                {defs.map((d) => <option key={d.key} value={d.key}>{d.name[lang]}</option>)}
              </select>{" "}
              <button onClick={async () => { await api.startProcess(startKey, clientId); setProcs(await api.processesFor(clientId)); }}>
                {tr("start")}
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

import { useEffect, useMemo, useState } from "react";
import { type Key } from "../i18n";
import { useUi } from "../ui";

/** The marketing_contact view says "none" when nothing was recorded. */
type ConsentState = "opt_in" | "opt_out" | "none";
interface Contact {
  id: string; name: string; name_translit_he: string; name_translit_lat: string; preferred_language: string;
  phone: string | null; email: string | null; consent_whatsapp: ConsentState; consent_email: ConsentState; consent_sms: ConsentState;
}
const CHANNELS = ["whatsapp", "email", "sms"] as const;
type Channel = (typeof CHANNELS)[number];

/** What marketing may see of a client: contact details, language and consent, nothing else. */
export function Contacts() {
  const { api, tr } = useUi();
  const [rows, setRows] = useState<Contact[] | null>(null);
  const [q, setQ] = useState("");
  const [only, setOnly] = useState<"" | Channel>("");
  const [error, setError] = useState(false);
  useEffect(() => { api.get<Contact[]>("/api/marketing/contacts").then(setRows).catch(() => setError(true)); }, [api]);
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (rows ?? []).filter((c) =>
      (only === "" || c[`consent_${only}` as const] === "opt_in") &&
      (needle === "" || [c.name, c.name_translit_he, c.name_translit_lat, c.phone ?? "", c.email ?? ""].some((v) => v.toLowerCase().includes(needle))));
  }, [rows, q, only]);
  return (
    <section aria-label={tr("contacts")}>
      <p>{tr("contactsNote")}</p>
      {error && <p role="alert">{tr("error")}</p>}
      <input type="search" aria-label={tr("search")} placeholder={tr("search")} value={q} onChange={(e) => setQ(e.target.value)} />{" "}
      <label>{tr("optedInTo")}{" "}
        <select value={only} onChange={(e) => setOnly(e.target.value as "" | Channel)}>
          <option value="">-</option>
          {CHANNELS.map((c) => <option key={c} value={c}>{tr(`channel.${c}` as Key)}</option>)}
        </select>
      </label>
      {rows && shown.length === 0 && <p>{tr("noResults")}</p>}
      <table>
        <tbody>
          {shown.map((c) => (
            <tr key={c.id}>
              <td><bdi>{c.name}</bdi></td>
              <td>{c.preferred_language}</td>
              <td dir="ltr">{c.phone}</td>
              <td dir="ltr">{c.email}</td>
              {CHANNELS.map((ch) => {
                const st = c[`consent_${ch}` as const];
                return <td key={ch} data-channel={ch}>{tr(`channel.${ch}` as Key)}: {st === "none" ? tr("never") : tr(`state.${st}` as Key)}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

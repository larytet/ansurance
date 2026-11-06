import { useEffect, useState } from "react";
import { type Key } from "../i18n";
import { useUi } from "../ui";

interface Data {
  licences: { user: string; licence_no: string; expires_on: string; state: string; cpd_missing_hours: number }[];
  kyc_expiring: number; complaints_unanswered: number; complaints_overdue: number; privacy_requests_open: number;
}

export function Compliance() {
  const { api, tr } = useUi();
  const [d, setD] = useState<Data | null>(null);
  useEffect(() => { api.get<Data>("/api/compliance").then(setD).catch(() => setD(null)); }, [api]);
  if (!d) return null;
  return (
    <div className="compliance">
      <h2>{tr("compliance")}</h2>
      <p>{tr("kycExpiring")}: {d.kyc_expiring} · {tr("complaintsOpen")}: {d.complaints_unanswered} · {tr("privacyOpen")}: {d.privacy_requests_open}</p>
      <table>
        <caption>{tr("licences")}</caption>
        <thead><tr><th>#</th><th>{tr("expires")}</th><th>{tr("status")}</th><th>{tr("cpdMissing")}</th></tr></thead>
        <tbody>
          {d.licences.map((l) => (
            <tr key={l.licence_no} className={l.state === "valid" ? undefined : "attention"}>
              <td><bdi>{l.user}</bdi> {l.licence_no}</td><td>{l.expires_on}</td><td>{tr(`lic.${l.state}` as Key)}</td><td>{l.cpd_missing_hours}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

import { useCallback, useEffect, useState } from "react";
import { ApiError, type IntakeRow } from "../api";
import { useUi } from "../ui";

function CandidateName({ id }: { id: string }) {
  const { api, tr } = useUi();
  const [name, setName] = useState("");
  useEffect(() => {
    api.client(id).then((c) => setName(c.name)).catch(() => setName(""));
  }, [api, id]);
  return <bdi>{name || tr("unknownClient")}</bdi>;
}

export function IntakeReview() {
  const { api, tr } = useUi();
  const [rows, setRows] = useState<{ review: IntakeRow[]; unassigned: IntakeRow[] } | null>(null);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const [review, unassigned] = await Promise.all([api.intake("review"), api.intake("unassigned")]);
      setRows({ review, unassigned });
    } catch {
      setMessage(tr("error"));
    }
  }, [api, tr]);
  useEffect(() => { void load(); }, [load]);

  async function confirm(item: IntakeRow, clientId: string) {
    setMessage("");
    try {
      await api.confirmIntake(item.id, clientId);
      await load();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.code : tr("error"));
    }
  }

  const list = (title: string, items: IntakeRow[]) => (
    <>
      <h2>{title} ({items.length})</h2>
      <ul className="scans">
        {items.map((i) => (
          <li key={i.id}>
            <strong>{i.filename || i.channel}</strong> · {i.doc_type} ({Math.round(i.type_conf * 100)}%) · <small>{i.route_reason}</small>
            <ul>
              {i.candidates.map((c) => (
                <li key={c.client_id}>
                  <CandidateName id={c.client_id} /> · {Math.round(c.score * 100)}% · {tr("evidence")}: {c.evidence.join(", ")}
                  {c.weak && <em> · {tr("weakMatch")}</em>}{" "}
                  <button onClick={() => void confirm(i, c.client_id)}>{tr("confirmTo")}</button>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </>
  );

  return (
    <section aria-label={tr("intake")}>
      {message && <p role="alert">{message}</p>}
      {rows && rows.review.length + rows.unassigned.length === 0 && <p>{tr("noScans")}</p>}
      {rows && rows.review.length > 0 && list(tr("reviewQueue"), rows.review)}
      {rows && rows.unassigned.length > 0 && list(tr("unassignedQueue"), rows.unassigned)}
    </section>
  );
}

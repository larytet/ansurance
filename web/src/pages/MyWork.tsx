import { useEffect, useState } from "react";
import { type WorkItem } from "../api";
import { useUi } from "../ui";

export function MyWork({ onOpenClient }: { onOpenClient: (id: string) => void }) {
  const { api, tr, me } = useUi();
  const [items, setItems] = useState<WorkItem[] | null>(null);
  const [error, setError] = useState(false);
  const canOpen = me?.capabilities.includes("client.read") ?? false;

  useEffect(() => {
    api.myWork().then((r) => setItems(r.items)).catch(() => setError(true));
  }, [api]);

  return (
    <section aria-label={tr("mywork")}>
      {error && <p role="alert">{tr("error")}</p>}
      {items && items.length === 0 && <p>{tr("noWork")}</p>}
      <ul className="work">
        {(items ?? []).map((w) => (
          <li key={`${w.instance_id}-${w.stage}`}>
            <strong>{w.title}</strong> · {tr("stage")}: {w.stage} · <small>{w.stage_status}</small>{" "}
            {canOpen && w.client_id && <button className="link" onClick={() => onOpenClient(w.client_id as string)}>{tr("openClient")}</button>}
          </li>
        ))}
      </ul>
    </section>
  );
}

import { useEffect, useState } from "react";
import { type Desk as Data } from "../api";
import { type Key } from "../i18n";
import { useUi } from "../ui";

export function Desk() {
  const { api, tr } = useUi();
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    api.desk().then(setData).catch(() => setError(true));
  }, [api]);
  return (
    <section aria-label={tr("desk")}>
      {error && <p role="alert">{tr("error")}</p>}
      {data && (
        <>
          <p>{tr("totalOpen")}: {data.total_open} · {tr("urgent")}: {data.total_urgent}</p>
          <ul className="cards">
            {data.areas.map((a) => (
              <li key={a.area} className={a.urgent > 0 ? "attention" : undefined}>
                <strong>{a.open}</strong> <span>{tr(`area.${a.area}` as Key)}</span>
                {a.urgent > 0 && <small> · {tr("urgent")}: {a.urgent}</small>}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

import { useEffect, useState } from "react";
import { ApiError } from "./api";
import { Clients } from "./pages/Clients";
import { Inbox } from "./pages/Inbox";
import { Login } from "./pages/Login";
import { useUi } from "./ui";

type Tab = "inbox" | "clients";

export function App() {
  const { api, me, setMe, tr, lang, setLang, theme, setTheme } = useUi();
  const [ready, setReady] = useState(false);
  const [tab, setTab] = useState<Tab>("inbox");

  useEffect(() => {
    api
      .me()
      .then(setMe)
      .catch((e) => {
        if (!(e instanceof ApiError)) console.error(e);
      })
      .finally(() => setReady(true));
  }, [api, setMe]);

  if (!ready) return <p>{tr("loading")}</p>;
  if (!me) return <Login />;

  const tabs: Tab[] = ["inbox", ...(me.capabilities.includes("client.read") ? (["clients"] as Tab[]) : [])];
  return (
    <div className="shell">
      <header>
        <strong>{tr("app")}</strong>
        <nav aria-label="main">
          {tabs.map((x) => (
            <button key={x} aria-current={tab === x ? "page" : undefined} onClick={() => setTab(x)}>
              {tr(x)}
            </button>
          ))}
        </nav>
        <span className="who">
          {me.name} · {me.roles.map((r) => tr(`role.${r}` as never)).join(", ")}
        </span>
        <button onClick={() => setLang(lang === "he" ? "en" : "he")} aria-label={tr("language")}>
          {lang === "he" ? "EN" : "עב"}
        </button>
        <button onClick={() => setTheme(theme === "light" ? "dark" : "light")} aria-label={tr("theme")}>
          {theme === "light" ? tr("dark") : tr("light")}
        </button>
        <button
          onClick={async () => {
            await api.logout().catch(() => undefined);
            setMe(null);
          }}
        >
          {tr("logout")}
        </button>
      </header>
      {tab === "inbox" ? <Inbox /> : <Clients />}
    </div>
  );
}

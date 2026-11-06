import { useEffect, useState } from "react";
import { ApiError } from "./api";
import { ClientFile } from "./pages/ClientFile";
import { Clients } from "./pages/Clients";
import { Campaigns } from "./pages/Campaigns";
import { Inbox } from "./pages/Inbox";
import { MyWork } from "./pages/MyWork";
import { Overview } from "./pages/Overview";
import { Login } from "./pages/Login";
import { useUi } from "./ui";

type Tab = "overview" | "inbox" | "mywork" | "clients" | "campaigns";

export function App() {
  const { api, me, setMe, tr, lang, setLang, theme, setTheme } = useUi();
  const [ready, setReady] = useState(false);
  const [tab, setTab] = useState<Tab>("inbox");
  const [open, setOpen] = useState<string | null>(null);

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

  const has = (c: string) => me.capabilities.includes(c);
  const tabs: Tab[] = [
    ...(has("flow.approve") ? (["overview"] as Tab[]) : []),
    "inbox",
    ...(has("process.read") ? (["mywork"] as Tab[]) : []),
    ...(has("client.read") ? (["clients"] as Tab[]) : []),
    ...(has("campaign.write") ? (["campaigns"] as Tab[]) : []),
  ];
  const openClient = (id: string) => { setTab("clients"); setOpen(id); };
  return (
    <div className="shell">
      <header>
        <strong>{tr("app")}</strong>
        <nav aria-label="main">
          {tabs.map((x) => (
            <button key={x} aria-current={tab === x ? "page" : undefined} onClick={() => { setTab(x); setOpen(null); }}>
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
      {tab === "overview" ? <Overview /> : tab === "inbox" ? <Inbox /> : tab === "mywork" ? <MyWork onOpenClient={openClient} /> : tab === "campaigns" ? <Campaigns /> : open ? <ClientFile clientId={open} onBack={() => setOpen(null)} /> : <Clients onOpen={setOpen} />}
    </div>
  );
}

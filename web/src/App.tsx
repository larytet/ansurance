import { useEffect, useState } from "react";
import { ApiError } from "./api";
import { ClientFile } from "./pages/ClientFile";
import { Clients } from "./pages/Clients";
import { Campaigns } from "./pages/Campaigns";
import { Commissions } from "./pages/Commissions";
import { Complaints } from "./pages/Complaints";
import { Desk } from "./pages/Desk";
import { Inbox } from "./pages/Inbox";
import { IntakeReview } from "./pages/IntakeReview";
import { MyWork } from "./pages/MyWork";
import { Overview } from "./pages/Overview";
import { Tax } from "./pages/Tax";
import { Today } from "./pages/Today";
import { Login } from "./pages/Login";
import { useUi } from "./ui";

type Tab = "today" | "complaints" | "commissions" | "tax" | "overview" | "inbox" | "desk" | "intake" | "mywork" | "clients" | "campaigns";

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
    ...(me.roles.includes("agent") ? (["today"] as Tab[]) : []),
    ...(has("flow.approve") ? (["overview"] as Tab[]) : []),
    "inbox",
    ...(has("desk.read") ? (["desk"] as Tab[]) : []),
    ...(has("intake.review") ? (["intake"] as Tab[]) : []),
    ...(has("process.read") ? (["mywork"] as Tab[]) : []),
    ...(has("client.read") ? (["clients"] as Tab[]) : []),
    ...(has("campaign.write") ? (["campaigns"] as Tab[]) : []),
    ...(has("complaint.write") ? (["complaints"] as Tab[]) : []),
    ...(has("commission.manage") ? (["commissions"] as Tab[]) : []),
    ...(has("tax.read") ? (["tax"] as Tab[]) : []),
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
      {tab === "today" ? <Today onOpenClient={openClient} /> : tab === "complaints" ? <Complaints /> : tab === "commissions" ? <Commissions /> : tab === "tax" ? <Tax /> : tab === "overview" ? <Overview /> : tab === "inbox" ? <Inbox /> : tab === "desk" ? <Desk /> : tab === "intake" ? <IntakeReview /> : tab === "mywork" ? <MyWork onOpenClient={openClient} /> : tab === "campaigns" ? <Campaigns /> : open ? <ClientFile clientId={open} onBack={() => setOpen(null)} /> : <Clients onOpen={setOpen} />}
    </div>
  );
}

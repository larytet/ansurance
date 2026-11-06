import { LANGS, LANG_NAMES, type Key, type Lang } from "../i18n";
import { useUi } from "../ui";

export function Settings() {
  const { me, tr, lang, setLang, theme, setTheme } = useUi();
  if (!me) return null;
  return (
    <section aria-label={tr("settings")}>
      <h2>{tr("account")}</h2>
      <p><bdi>{me.name}</bdi> · {me.email} · {me.roles.map((r) => tr(`role.${r}` as Key)).join(", ")}</p>
      <label>{tr("language")}{" "}
        <select value={lang} onChange={(e) => setLang(e.target.value as Lang)}>
          {LANGS.map((l) => <option key={l} value={l}>{LANG_NAMES[l]}</option>)}
        </select>
      </label>
      <label>{tr("theme")}{" "}
        <select value={theme} onChange={(e) => setTheme(e.target.value as "light" | "dark")}>
          <option value="light">{tr("light")}</option>
          <option value="dark">{tr("dark")}</option>
        </select>
      </label>
      <h2>{tr("capabilities")}</h2>
      <ul className="caps">{me.capabilities.map((c) => <li key={c}><code>{c}</code></li>)}</ul>
    </section>
  );
}

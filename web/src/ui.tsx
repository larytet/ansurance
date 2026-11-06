import { type ReactNode, createContext, useContext, useEffect, useState } from "react";
import { Api, type Me } from "./api";
import { type Key, type Lang, dirOf, t } from "./i18n";

export type Theme = "light" | "dark";

interface Ctx {
  api: Api;
  me: Me | null;
  setMe: (m: Me | null) => void;
  lang: Lang;
  setLang: (l: Lang) => void;
  theme: Theme;
  setTheme: (t: Theme) => void;
  tr: (k: Key) => string;
}

const UiContext = createContext<Ctx | null>(null);

function remembered<T extends string>(key: string, fallback: T, allowed: readonly T[]): T {
  try {
    const v = localStorage.getItem(key);
    if (v && (allowed as readonly string[]).includes(v)) return v as T;
  } catch {
    /* storage may be unavailable */
  }
  return fallback;
}

export function UiProvider({ api, children }: { api: Api; children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [lang, setLang] = useState<Lang>(() => remembered("lang", "he", ["he", "en"]));
  const [theme, setTheme] = useState<Theme>(() => remembered("theme", "light", ["light", "dark"]));

  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = dirOf(lang);
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("lang", lang);
      localStorage.setItem("theme", theme);
    } catch {
      /* ignore */
    }
  }, [lang, theme]);

  const value: Ctx = { api, me, setMe, lang, setLang, theme, setTheme, tr: (k) => t(lang, k) };
  return <UiContext.Provider value={value}>{children}</UiContext.Provider>;
}

export function useUi(): Ctx {
  const c = useContext(UiContext);
  if (!c) throw new Error("UiProvider missing");
  return c;
}

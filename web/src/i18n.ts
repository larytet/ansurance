export type Lang = "he" | "en";

const dict = {
  en: {
    app: "ansurance", email: "E-mail", password: "Password", code: "Authenticator or recovery code",
    login: "Sign in", logout: "Sign out", loginFailed: "Sign-in failed. Check the details and try again.",
    locked: "Too many attempts. Try again later.", loading: "Loading…",
    inbox: "Inbox", clients: "Clients", open: "Open", urgent: "Urgent", doneToday: "Done today",
    done: "Done", dismiss: "Dismiss", reassign: "Reassign", reason: "Reason (required)", note: "Note (required)",
    confirm: "Confirm", cancel: "Cancel", empty: "Nothing waiting.", more: "Load more",
    search: "Search by name in any script, or any digits of the ID", noResults: "No clients match.",
    allUrgencies: "All", important: "Important", normal: "Normal", role: "Role",
    theme: "Theme", light: "Light", dark: "Dark", language: "Language",
    "role.agent": "Agent", "role.backoffice": "Back-office", "role.marketing": "Marketing", "role.manager": "Manager",
    readOnly: "Read-only view", error: "Something went wrong.",
  },
  he: {
    app: "ansurance", email: "דוא״ל", password: "סיסמה", code: "קוד מאמת או קוד שחזור",
    login: "כניסה", logout: "יציאה", loginFailed: "הכניסה נכשלה. בדקו את הפרטים ונסו שוב.",
    locked: "יותר מדי ניסיונות. נסו שוב מאוחר יותר.", loading: "טוען…",
    inbox: "תיבה", clients: "לקוחות", open: "פתוחים", urgent: "דחופים", doneToday: "הושלמו היום",
    done: "בוצע", dismiss: "דחייה", reassign: "העברה", reason: "סיבה (חובה)", note: "הערה (חובה)",
    confirm: "אישור", cancel: "ביטול", empty: "אין פריטים ממתינים.", more: "טען עוד",
    search: "חיפוש לפי שם בכל כתב, או כל ספרות מהזהות", noResults: "לא נמצאו לקוחות.",
    allUrgencies: "הכול", important: "חשוב", normal: "רגיל", role: "תפקיד",
    theme: "ערכת נושא", light: "בהירה", dark: "כהה", language: "שפה",
    "role.agent": "סוכן", "role.backoffice": "משרד אחורי", "role.marketing": "שיווק", "role.manager": "מנהל",
    readOnly: "תצוגה לקריאה בלבד", error: "משהו השתבש.",
  },
} as const;

export type Key = keyof (typeof dict)["en"];
export const t = (lang: Lang, key: Key): string => dict[lang][key];
export const dirOf = (lang: Lang): "rtl" | "ltr" => (lang === "he" ? "rtl" : "ltr");

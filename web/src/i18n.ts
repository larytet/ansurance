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
    back: "Back to the list", details: "Details", policies: "Policies", documents: "Documents", consent: "Consent",
    processes: "Processes", phone: "Phone", mail: "E-mail address", idNumber: "ID number", status: "Status",
    family: "Family", noPolicies: "No policies.", noDocuments: "No documents.", noProcesses: "No running processes.",
    renewal: "Renewal", premium: "Premium", upload: "Upload a document", docType: "Type", choose: "Choose a file",
    download: "Download", restricted: "Restricted", legalHold: "Legal hold", scan_clean: "Scanned clean",
    scan_pending: "Waiting for the virus scan", scan_infected: "Quarantined: infected",
    optIn: "Opt in", optOut: "Opt out", never: "No consent recorded", source: "Source of the consent (required)",
    startProcess: "Start a process", start: "Start", record: "Record",
    "channel.whatsapp": "WhatsApp", "channel.email": "E-mail", "channel.sms": "SMS", "channel.phone": "Phone", "channel.post": "Post",
    "state.opt_in": "Opted in", "state.opt_out": "Opted out",
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
    back: "חזרה לרשימה", details: "פרטים", policies: "פוליסות", documents: "מסמכים", consent: "הסכמות",
    processes: "תהליכים", phone: "טלפון", mail: "כתובת דוא״ל", idNumber: "מספר זהות", status: "סטטוס",
    family: "משפחה", noPolicies: "אין פוליסות.", noDocuments: "אין מסמכים.", noProcesses: "אין תהליכים פעילים.",
    renewal: "חידוש", premium: "פרמיה", upload: "העלאת מסמך", docType: "סוג", choose: "בחרו קובץ",
    download: "הורדה", restricted: "מוגבל", legalHold: "עיכוב משפטי", scan_clean: "נסרק, תקין",
    scan_pending: "ממתין לסריקת וירוסים", scan_infected: "בהסגר: נגוע",
    optIn: "הסכמה", optOut: "הסרה", never: "לא נרשמה הסכמה", source: "מקור ההסכמה (חובה)",
    startProcess: "פתיחת תהליך", start: "פתיחה", record: "רישום",
    "channel.whatsapp": "וואטסאפ", "channel.email": "דוא״ל", "channel.sms": "הודעת SMS", "channel.phone": "טלפון", "channel.post": "דואר",
    "state.opt_in": "הסכים/ה", "state.opt_out": "הוסר/ה",
  },
} as const;

export type Key = keyof (typeof dict)["en"];
export const t = (lang: Lang, key: Key): string => dict[lang][key];
export const dirOf = (lang: Lang): "rtl" | "ltr" => (lang === "he" ? "rtl" : "ltr");

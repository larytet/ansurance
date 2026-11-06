export interface Health {
  status: string;
  version: string;
  profile: string;
}

export async function fetchHealth(fetcher: typeof fetch = fetch): Promise<Health> {
  const r = await fetcher("/api/health");
  if (!r.ok) throw new Error(`health ${r.status}`);
  return (await r.json()) as Health;
}

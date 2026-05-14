const DEFAULT_API = "http://127.0.0.1:8001";

export function getApiBase(): string {
  const fromEnv = import.meta.env.VITE_API_BASE?.trim();
  return (fromEnv || DEFAULT_API).replace(/\/$/, "");
}

export function apiPath(path: string): string {
  const base = getApiBase();
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${base}${p}`;
}

export async function fetchAPI<T>(path: string): Promise<T> {
  // Calls /api/* which middleware rewrites to the backend with API key injected server-side.
  const res = await fetch(path);
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${res.statusText}`);
  }
  return res.json();
}

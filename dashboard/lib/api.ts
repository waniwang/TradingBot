const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "dev-key";

export async function fetchAPI<T>(path: string): Promise<T> {
  // Calls /api/* which Next.js rewrites to the backend server.
  // This avoids HTTPS→HTTP mixed content issues.
  const res = await fetch(path, {
    headers: { "X-API-Key": API_KEY },
  });
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${res.statusText}`);
  }
  return res.json();
}

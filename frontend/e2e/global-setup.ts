import type { FullConfig } from "@playwright/test";

/** Warm the stack before the suite runs.
 *
 *  The first request to each route pays for Next.js route compilation and the
 *  first SQLAlchemy query on a cold connection pool - together roughly 17s on
 *  a cold `/stocks`. With several workers those cold starts overlap and blow
 *  the per-test timeout, so a different test failed on every run while the
 *  application itself was healthy. Warming first makes a failure mean
 *  something is actually broken.
 */
async function warm(url: string): Promise<void> {
  try {
    await fetch(url, { signal: AbortSignal.timeout(120_000) });
  } catch {
    // A warm-up miss is not a test failure; the test itself will report it.
  }
}

export default async function globalSetup(config: FullConfig): Promise<void> {
  const base = config.projects[0]?.use?.baseURL ?? "http://127.0.0.1:3000";
  const api = process.env.NEXT_PUBLIC_API_URL?.trim() || `${base}/api/v1`;

  const pages = ["/", "/stocks", "/bonds", "/news", "/portfolio", "/watchlist",
                 "/settings", "/system", "/compare", "/stock/HSBK"];
  const endpoints = ["/stocks?limit=40", "/bonds?limit=40", "/news?limit=20",
                     "/settings", "/health", "/health/kase", "/health/monitoring",
                     "/health/subsystems", "/portfolios"];

  await Promise.all([
    ...pages.map((path) => warm(`${base}${path}`)),
    ...endpoints.map((path) => warm(`${api}${path}`)),
  ]);
}

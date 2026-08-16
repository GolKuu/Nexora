const backendOrigin =
  process.env.BACKEND_ORIGIN?.trim().replace(/\/$/, "") ||
  "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  env: {
    // Resolved at build time; the browser talks to the backend through this.
    NEXT_PUBLIC_API_URL:
      process.env.NEXT_PUBLIC_API_URL?.trim() || "/api/v1",
  },
  // In local development the frontend and FastAPI run as separate processes.
  // Keep browser requests same-origin and proxy them server-side. Vercel's
  // repository-level rewrite handles the same path in production.
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${backendOrigin}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;

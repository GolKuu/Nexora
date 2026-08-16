/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  env: {
    // Resolved at build time; the browser talks to the backend through this.
    NEXT_PUBLIC_API_URL:
      process.env.NEXT_PUBLIC_API_URL?.trim() || "/api/v1",
  },
};

export default nextConfig;

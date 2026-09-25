/** @type {import('next').NextConfig} */
// API_URL: where the FastAPI backend runs. The browser always calls same-origin /api/*,
// and Next.js proxies it there, so no CORS configuration is needed.
const API_URL = (process.env.API_URL || "http://localhost:8000").replace(/\/$/, "");

const nextConfig = {
  output: process.env.VERCEL ? undefined : "standalone",
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
};
export default nextConfig;

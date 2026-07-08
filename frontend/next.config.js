/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',
  // P1 §1: directory-style export (market/index.html …) so Starlette
  // StaticFiles(html=True) serves deep links — /market 307→ /market/ → hit.
  trailingSlash: true,
  images: { unoptimized: true },
};

module.exports = nextConfig;

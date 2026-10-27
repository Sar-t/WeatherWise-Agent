import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  devIndicators: false,
  // The e2e tests build into their own folder, so they never overwrite a build being served.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;

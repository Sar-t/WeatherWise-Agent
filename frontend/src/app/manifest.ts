import type { MetadataRoute } from "next";

// Lets WeatherWise be added to a phone's home screen. No service worker: it needs the network anyway.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "WeatherWise",
    short_name: "WeatherWise",
    description: "Weather, air quality and the best time to step out, for India and beyond.",
    start_url: "/",
    display: "standalone",
    background_color: "#f6f5f2",
    theme_color: "#f6f5f2",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}

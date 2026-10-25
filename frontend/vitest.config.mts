import { tmpdir } from "node:os";
import { join } from "node:path";
import react from "@vitejs/plugin-react";
import tsconfigPaths from "vite-tsconfig-paths";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [tsconfigPaths(), react()],
  test: {
    // Outside node_modules: cache writes there stall the Next dev server's file watcher.
    fsModuleCachePath: join(tmpdir(), "weatherwise-vitest"),
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});

"use client";

import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const TEN_MINUTES = 10 * 60 * 1000;

let browserQueryClient: QueryClient | undefined;

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: TEN_MINUTES,
        refetchInterval: TEN_MINUTES,
        refetchOnWindowFocus: true,
        retry: (failures, error) => {
          // Don't retry answers that won't change: bad input, ambiguity, limits.
          const status = (error as { status?: number }).status ?? 0;
          return status >= 500 || status === 0 ? failures < 2 : false;
        },
      },
    },
  });
}

function getQueryClient() {
  // Keep server requests isolated and preserve the browser cache across renders.
  if (typeof window === "undefined") return makeQueryClient();
  browserQueryClient ??= makeQueryClient();
  return browserQueryClient;
}

export function Providers({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={getQueryClient()}>{children}</QueryClientProvider>;
}

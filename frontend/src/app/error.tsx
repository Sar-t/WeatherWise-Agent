"use client"; // error boundaries are Client Components

import { ErrorPage } from "@/components/ErrorPage";

export default function Error({ error, retry }: { error: unknown; retry: () => void }) {
  return <ErrorPage error={error} retry={retry} />;
}

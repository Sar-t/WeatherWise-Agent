import { Suspense } from "react";
import { Dashboard } from "@/components/Dashboard";
import { DashboardSkeleton } from "@/components/States";

export default function Home() {
  // Dashboard reads ?place= (useSearchParams), so it renders inside a Suspense boundary.
  return (
    <Suspense
      fallback={
        <main className="mx-auto w-full max-w-6xl px-4 pt-24 sm:px-6 lg:px-8">
          <DashboardSkeleton />
        </main>
      }
    >
      <Dashboard />
    </Suspense>
  );
}

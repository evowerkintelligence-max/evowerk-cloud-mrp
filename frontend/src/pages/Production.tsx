import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "@/components/PageHeader";
import { PageTabs } from "@/components/PageTabs";
import { useCurrentUser } from "@/lib/currentUser";
import { JobsPanel } from "./production/JobsPanel";
import { WorkCentersPanel } from "./production/WorkCentersPanel";
import { MachinesPanel } from "./production/MachinesPanel";
import { OperatorsPanel } from "./production/OperatorsPanel";
import { RoutingsPanel } from "./production/RoutingsPanel";

const SETUP_TABS = [
  { value: "work-centers", label: "Work Centers" },
  { value: "machines", label: "Machines" },
  { value: "operators", label: "Operators" },
  { value: "routings", label: "Routings" },
] as const;
const ALL_TABS = [{ value: "jobs", label: "Jobs" } as const, ...SETUP_TABS];

type Tab = (typeof ALL_TABS)[number]["value"];

export function Production() {
  const org = useCurrentUser();
  // enabled_features isn't known until org-me resolves - default optimistically to
  // "jobs" (the common case) rather than the setup tabs, and only bump off it once
  // loading has actually settled and the org turns out not to have the feature.
  const hasJobs = (org.data?.enabled_features ?? []).includes("production");
  const tabs = hasJobs ? ALL_TABS : SETUP_TABS;
  const tabValues = new Set<string>(tabs.map((t) => t.value));

  const [searchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const [tab, setTab] = useState<Tab>(
    requestedTab && tabValues.has(requestedTab) ? (requestedTab as Tab) : "jobs",
  );

  useEffect(() => {
    if (!org.isLoading && !hasJobs && tab === "jobs") setTab("work-centers");
  }, [org.isLoading, hasJobs, tab]);

  return (
    <div>
      <PageHeader
        title="Production"
        description="The jobs on the floor, and the work centers, machines, and routings behind them."
        actions={<PageTabs tabs={[...tabs]} active={tab} onChange={setTab} />}
      />
      <div key={tab} className="animate-fade-in">
        {tab === "jobs" && hasJobs && <JobsPanel />}
        {tab === "work-centers" && <WorkCentersPanel />}
        {tab === "machines" && <MachinesPanel />}
        {tab === "operators" && <OperatorsPanel />}
        {tab === "routings" && <RoutingsPanel />}
      </div>
    </div>
  );
}

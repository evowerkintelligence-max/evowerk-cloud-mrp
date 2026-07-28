import { PageHeader } from "@/components/PageHeader";
import { ExploreGrid } from "@/components/ExploreGrid";
import { useCurrentUser } from "@/lib/currentUser";
import { NAV_SECTIONS, isNavItemVisible } from "@/lib/nav";

/** Explore hub for one sidebar section (see lib/nav.ts's NAV_SECTIONS) -
 * reachable from the Dashboard's 4 Explore category cards. Reads straight
 * from NAV_SECTIONS so it can't drift out of sync with the sidebar. */
function CategoryHub({ sectionLabel, description }: { sectionLabel: string; description: string }) {
  const org = useCurrentUser();
  const enabledFeatures = new Set(org.data?.enabled_features ?? []);
  const section = NAV_SECTIONS.find((s) => s.label === sectionLabel);
  const items = (section?.items ?? []).filter((item) => isNavItemVisible(item, { enabledFeatures }));

  return (
    <div>
      <PageHeader title={sectionLabel} description={description} />
      <ExploreGrid items={items} />
    </div>
  );
}

export function OperationsHub() {
  return (
    <CategoryHub
      sectionLabel="Operations"
      description="BOMs, procurement, planning, and everything on the shop floor."
    />
  );
}

export function DataReportsHub() {
  return (
    <CategoryHub
      sectionLabel="Data & Reports"
      description="Upload data, query it, and turn it into charts and reports."
    />
  );
}

export function AnalyticsHub() {
  return (
    <CategoryHub
      sectionLabel="Plant Analytics"
      description="OEE, downtime, throughput, scrap, and shifts."
    />
  );
}

export function SystemHub() {
  return (
    <CategoryHub
      sectionLabel="System"
      description="Integrations, live streams, workflows, and alerts."
    />
  );
}

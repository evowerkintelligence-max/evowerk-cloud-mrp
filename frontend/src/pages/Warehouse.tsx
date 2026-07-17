import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { PageTabs } from "@/components/PageTabs";
import { InventoryPanel } from "./Inventory";
import { LocationsPanel } from "./Locations";
import { ApprovalsPanel } from "./Approvals";

interface OrgMeResponse {
  role: string;
}

const BASE_TABS = [
  { value: "inventory", label: "Inventory" },
  { value: "locations", label: "Locations" },
] as const;

const APPROVALS_TAB = { value: "approvals", label: "Approvals" } as const;

type Tab = "inventory" | "locations" | "approvals";

export function Warehouse() {
  const [tab, setTab] = useState<Tab>("inventory");

  const org = useQuery<OrgMeResponse>({
    queryKey: ["org-me"],
    queryFn: async () => {
      const res = await apiFetch("/orgs/me");
      if (!res.ok) throw new Error("Failed to load organization");
      return res.json();
    },
  });
  const canSeeApprovals = org.data?.role === "owner" || org.data?.role === "manager";
  const tabs = canSeeApprovals ? [...BASE_TABS, APPROVALS_TAB] : [...BASE_TABS];

  return (
    <div>
      <PageHeader
        title="Warehouse"
        description="On-hand stock by location, and the locations that hold it. Receiving a PO adds stock; consume, adjust, or transfer to move it. (Lot-tracked items track on-hand through their lots.)"
        actions={<PageTabs tabs={tabs} active={tab} onChange={setTab} />}
      />
      <div key={tab} className="animate-fade-in">
        {tab === "inventory" && <InventoryPanel />}
        {tab === "locations" && <LocationsPanel />}
        {tab === "approvals" && canSeeApprovals && <ApprovalsPanel />}
      </div>
    </div>
  );
}

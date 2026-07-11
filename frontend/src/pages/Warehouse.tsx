import { useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { PageTabs } from "@/components/PageTabs";
import { InventoryPanel } from "./Inventory";
import { LocationsPanel } from "./Locations";

const TABS = [
  { value: "inventory", label: "Inventory" },
  { value: "locations", label: "Locations" },
] as const;

type Tab = (typeof TABS)[number]["value"];

export function Warehouse() {
  const [tab, setTab] = useState<Tab>("inventory");

  return (
    <div>
      <PageHeader
        title="Warehouse"
        description="On-hand stock by location, and the locations that hold it. Receiving a PO adds stock; consume, adjust, or transfer to move it. (Lot-tracked items track on-hand through their lots.)"
        actions={<PageTabs tabs={[...TABS]} active={tab} onChange={setTab} />}
      />
      {tab === "inventory" ? <InventoryPanel /> : <LocationsPanel />}
    </div>
  );
}

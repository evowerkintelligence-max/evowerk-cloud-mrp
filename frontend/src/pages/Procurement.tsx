import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "@/components/PageHeader";
import { PageTabs } from "@/components/PageTabs";
import { PurchaseOrdersPanel } from "./PurchaseOrders";
import { SuppliersPanel } from "./Suppliers";
import { GeneratePosPanel } from "./GeneratePos";
import { BuildCalculatorPanel } from "./BuildCalculator";

const TABS = [
  { value: "purchase-orders", label: "Purchase Orders" },
  { value: "suppliers", label: "Suppliers" },
  { value: "generate", label: "Generate from Plan" },
  { value: "build-calculator", label: "Build Calculator" },
] as const;

type Tab = (typeof TABS)[number]["value"];

const TAB_VALUES = new Set<string>(TABS.map((t) => t.value));

export function Procurement() {
  const [searchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const [tab, setTab] = useState<Tab>(
    requestedTab && TAB_VALUES.has(requestedTab) ? (requestedTab as Tab) : "purchase-orders",
  );

  return (
    <div>
      <PageHeader
        title="Procurement"
        description="Vendors and the purchase orders you place with them."
        actions={<PageTabs tabs={[...TABS]} active={tab} onChange={setTab} />}
      />
      <div key={tab} className="animate-fade-in">
        {tab === "purchase-orders" && <PurchaseOrdersPanel />}
        {tab === "suppliers" && <SuppliersPanel />}
        {tab === "generate" && (
          <GeneratePosPanel
            initialHorizonStart={searchParams.get("horizonStart")}
            initialHorizonEnd={searchParams.get("horizonEnd")}
          />
        )}
        {tab === "build-calculator" && <BuildCalculatorPanel />}
      </div>
    </div>
  );
}

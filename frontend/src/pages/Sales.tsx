import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "@/components/PageHeader";
import { PageTabs } from "@/components/PageTabs";
import { OrdersPanel } from "./sales/OrdersPanel";
import { CustomersPanel } from "./sales/CustomersPanel";

const TABS = [
  { value: "orders", label: "Orders" },
  { value: "customers", label: "Customers" },
] as const;

type Tab = (typeof TABS)[number]["value"];

const TAB_VALUES = new Set<string>(TABS.map((t) => t.value));

export function Sales() {
  const [searchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const [tab, setTab] = useState<Tab>(
    requestedTab && TAB_VALUES.has(requestedTab) ? (requestedTab as Tab) : "orders",
  );

  return (
    <div>
      <PageHeader
        title="Sales"
        description="Customers and the orders they have placed with you."
        actions={<PageTabs tabs={[...TABS]} active={tab} onChange={setTab} />}
      />
      <div key={tab} className="animate-fade-in">
        {tab === "orders" && <OrdersPanel />}
        {tab === "customers" && <CustomersPanel />}
      </div>
    </div>
  );
}

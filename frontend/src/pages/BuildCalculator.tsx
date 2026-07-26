import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { Field } from "@/components/Field";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { ItemSelect } from "@/components/ItemSelect";

interface ItemResponse {
  id: string;
  sku: string;
  name: string;
  item_type: string;
  unit_of_measure: string;
}

interface MrpPlanLine {
  item_id: string;
  item_sku: string;
  item_name: string;
  is_manufactured: boolean;
  on_hand: string;
  net_requirement: string;
  suggested_order_quantity: string;
}

interface BuildPlanResponse {
  item_id: string;
  item_sku: string;
  item_name: string;
  build_quantity: string;
  yield_loss_pct: string;
  lines: MrpPlanLine[];
}

interface GeneratePosResult {
  created: { po_id: string; po_number: number; supplier_name: string; line_count: number }[];
  unassigned: { item_id: string; item_sku: string; item_name: string; net_requirement: string }[];
}

export function BuildCalculatorPanel() {
  const [error, setError] = useState<string | null>(null);
  const [itemId, setItemId] = useState("");
  const [buildQuantity, setBuildQuantity] = useState("");
  const [yieldLossPct, setYieldLossPct] = useState("0");
  const [plan, setPlan] = useState<BuildPlanResponse | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<GeneratePosResult | null>(null);

  const items = useQuery<ItemResponse[]>({
    queryKey: ["items"],
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
      return res.json();
    },
  });

  const calculate = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/mrp/build-plan", {
        method: "POST",
        body: JSON.stringify({
          item_id: itemId,
          build_quantity: buildQuantity,
          yield_loss_pct: yieldLossPct || "0",
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to calculate build plan");
      }
      return res.json() as Promise<BuildPlanResponse>;
    },
    onSuccess: (data) => {
      // Guard against stale responses: only apply plan if response matches current input state
      if (
        data.item_id === itemId &&
        Number(data.build_quantity) === Number(buildQuantity) &&
        Number(data.yield_loss_pct) === Number(yieldLossPct || "0")
      ) {
        setPlan(data);
        setResult(null);
        setConfirming(false);
      }
      // If response is stale (inputs changed before response arrived), silently drop it
    },
    onError: (e: Error) => setError(e.message),
  });

  const commit = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/mrp/build-plan/generate-pos", {
        method: "POST",
        body: JSON.stringify({
          item_id: itemId,
          build_quantity: buildQuantity,
          yield_loss_pct: yieldLossPct || "0",
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create purchase orders");
      }
      return res.json() as Promise<GeneratePosResult>;
    },
    onSuccess: (data) => {
      setResult(data);
      setConfirming(false);
    },
    onError: (e: Error) => setError(e.message),
  });

  const orderableLines = (plan?.lines ?? []).filter(
    (line) => !line.is_manufactured && Number(line.net_requirement) > 0,
  );

  function onCalculate() {
    setError(null);
    if (!itemId) {
      setError("Pick a product to build");
      return;
    }
    if (!buildQuantity || Number(buildQuantity) <= 0) {
      setError("Enter a build quantity greater than 0");
      return;
    }
    calculate.mutate();
  }

  return (
    <div className="space-y-8">
      {error && <Alert variant="destructive">{error}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle>Build calculator</CardTitle>
          <CardDescription>
            Pick a manufactured item and how many you plan to build - this explodes its active recipe
            (with each component's own scrap %) into what you need to buy, netted against stock on hand.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3 lg:max-w-2xl">
            <Field label="Product" htmlFor="bc-item">
              <ItemSelect
                id="bc-item"
                items={items.data}
                value={itemId}
                onChange={(id) => {
                  setItemId(id);
                  setPlan(null);
                  setResult(null);
                  setConfirming(false);
                }}
                required
              />
            </Field>
            <Field label="Build quantity" htmlFor="bc-qty">
              <Input
                id="bc-qty"
                type="number"
                step="0.0001"
                min="0.0001"
                value={buildQuantity}
                onChange={(e) => {
                  setBuildQuantity(e.target.value);
                  setPlan(null);
                  setResult(null);
                  setConfirming(false);
                }}
              />
            </Field>
            <Field label="Expected yield loss %" htmlFor="bc-yield">
              <Input
                id="bc-yield"
                type="number"
                step="0.01"
                min="0"
                max="99.99"
                value={yieldLossPct}
                onChange={(e) => {
                  setYieldLossPct(e.target.value);
                  setPlan(null);
                  setResult(null);
                  setConfirming(false);
                }}
                title="Percent of build attempts expected to fail QA/scrap entirely - inflates the build quantity before exploding the recipe"
              />
            </Field>
          </div>
          <Button onClick={onCalculate} loading={calculate.isPending}>
            Calculate
          </Button>

          {plan && (
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Item</TH>
                    <TH>On hand</TH>
                    <TH>Net requirement</TH>
                    <TH>Suggested action</TH>
                  </TR>
                </THead>
                <TBody>
                  {plan.lines.map((line) => (
                    <TR key={line.item_id}>
                      <TD className="font-medium">
                        {line.item_sku} - {line.item_name}
                        {line.is_manufactured && (
                          <Badge variant="accent" className="ml-2">
                            make
                          </Badge>
                        )}
                      </TD>
                      <TD>{line.on_hand}</TD>
                      <TD className="font-semibold">{line.net_requirement}</TD>
                      <TD>
                        {!line.is_manufactured && Number(line.net_requirement) > 0 ? (
                          <Badge variant="info">Order {line.suggested_order_quantity}</Badge>
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          )}
        </CardContent>
      </Card>

      {plan && orderableLines.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Generate purchase orders</CardTitle>
            <CardDescription>
              Creates one draft PO per preferred supplier from the shortfalls above. Nothing is created
              until you confirm below.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {!confirming && !result && (
              <Button onClick={() => setConfirming(true)}>Review and generate purchase orders</Button>
            )}
            {confirming && (
              <div className="space-y-3 rounded-lg border border-border p-4">
                <p className="text-sm font-semibold">
                  About to order {orderableLines.length} item{orderableLines.length === 1 ? "" : "s"}:
                </p>
                <ul className="space-y-1 text-sm text-muted-foreground">
                  {orderableLines.map((line) => (
                    <li key={line.item_id}>
                      {line.item_sku} - {line.item_name}: {line.suggested_order_quantity}
                    </li>
                  ))}
                </ul>
                <div className="flex gap-2">
                  <Button onClick={() => commit.mutate()} loading={commit.isPending}>
                    Confirm &amp; create
                  </Button>
                  <Button variant="ghost" onClick={() => setConfirming(false)}>
                    Cancel
                  </Button>
                </div>
              </div>
            )}
            {result && (
              <div className="space-y-2 rounded-lg border border-border p-4">
                {result.created.length > 0 ? (
                  <ul className="space-y-1 text-sm">
                    {result.created.map((po) => (
                      <li key={po.po_id} className="flex items-center gap-2">
                        <Badge variant="success">Draft PO #{po.po_number}</Badge>
                        {po.supplier_name} · {po.line_count} line{po.line_count === 1 ? "" : "s"}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-muted-foreground">No POs created.</p>
                )}
                {result.unassigned.length > 0 && (
                  <Alert variant="warning">
                    No supplier set for: {result.unassigned.map((u) => u.item_sku).join(", ")}. Set one under
                    Items and order again.
                  </Alert>
                )}
                <p className="text-sm text-muted-foreground">
                  Find the new drafts under the Purchase Orders tab above.
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

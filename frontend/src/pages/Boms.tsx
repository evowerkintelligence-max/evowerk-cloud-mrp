import { FormEvent, useState } from "react";
import { Layers } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { humanize } from "@/lib/status";

interface ItemResponse {
  id: string;
  sku: string;
  name: string;
  item_type: string;
  unit_of_measure: string;
}

interface BomResponse {
  id: string;
  item_id: string;
  item_sku: string;
  item_name: string;
  version: number;
  status: string;
}

interface BomLineResponse {
  id: string;
  component_item_id: string;
  component_sku: string;
  component_name: string;
  unit_of_measure: string;
  quantity: string;
  sequence: number;
}

interface BomDetailResponse extends BomResponse {
  lines: BomLineResponse[];
}

function bomStatusVariant(status: string) {
  if (status === "active") return "success" as const;
  if (status === "archived") return "neutral" as const;
  return "warning" as const; // draft
}

export function Boms() {
  const queryClient = useQueryClient();
  const [selectedBomId, setSelectedBomId] = useState<string | null>(null);
  const [newBomItemId, setNewBomItemId] = useState("");
  const [lineComponentId, setLineComponentId] = useState("");
  const [lineQuantity, setLineQuantity] = useState("1");
  const [error, setError] = useState<string | null>(null);

  const items = useQuery<ItemResponse[]>({
    queryKey: ["items"],
    queryFn: async () => {
      const res = await apiFetch("/items");
      if (!res.ok) throw new Error("Failed to load items");
      return res.json();
    },
  });

  const boms = useQuery<BomResponse[]>({
    queryKey: ["boms"],
    queryFn: async () => {
      const res = await apiFetch("/boms");
      if (!res.ok) throw new Error("Failed to load BOMs");
      return res.json();
    },
  });

  const bomDetail = useQuery<BomDetailResponse>({
    queryKey: ["boms", selectedBomId],
    enabled: !!selectedBomId,
    queryFn: async () => {
      const res = await apiFetch(`/boms/${selectedBomId}`);
      if (!res.ok) throw new Error("Failed to load BOM");
      return res.json();
    },
  });

  const createBom = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/boms", {
        method: "POST",
        body: JSON.stringify({ item_id: newBomItemId }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create BOM");
      }
      return res.json() as Promise<BomResponse>;
    },
    onSuccess: (bom) => {
      queryClient.invalidateQueries({ queryKey: ["boms"] });
      setSelectedBomId(bom.id);
    },
    onError: (e: Error) => setError(e.message),
  });

  const addLine = useMutation({
    mutationFn: async () => {
      if (!bomDetail.data) throw new Error("No BOM selected");
      const existingLines = bomDetail.data.lines.map((l) => ({
        component_item_id: l.component_item_id,
        quantity: l.quantity,
        sequence: l.sequence,
      }));
      const res = await apiFetch(`/boms/${selectedBomId}`, {
        method: "PATCH",
        body: JSON.stringify([
          ...existingLines,
          { component_item_id: lineComponentId, quantity: lineQuantity, sequence: existingLines.length },
        ]),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to update lines");
      }
      return res.json();
    },
    onSuccess: () => {
      setLineComponentId("");
      setLineQuantity("1");
      queryClient.invalidateQueries({ queryKey: ["boms", selectedBomId] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const activateBom = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/boms/${selectedBomId}/activate`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to activate BOM");
      }
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["boms"] });
      queryClient.invalidateQueries({ queryKey: ["boms", selectedBomId] });
    },
    onError: (e: Error) => setError(e.message),
  });

  function onCreateBom(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createBom.mutate();
  }

  function onAddLine(e: FormEvent) {
    e.preventDefault();
    setError(null);
    addLine.mutate();
  }

  return (
    <div>
      <PageHeader
        title="Bills of Materials"
        description="Define the components that go into a manufactured item, then activate a version to use it in planning."
      />

      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Create a draft BOM</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onCreateBom} className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end">
            <Field label="For item" htmlFor="bom-item">
              <Select
                id="bom-item"
                value={newBomItemId}
                onChange={(e) => setNewBomItemId(e.target.value)}
                required
              >
                <option value="" disabled>
                  Select an item
                </option>
                {items.data?.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.sku} - {item.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Button type="submit" loading={createBom.isPending}>
              Create draft
            </Button>
          </form>
        </CardContent>
      </Card>

      {boms.data && boms.data.length === 0 ? (
        <EmptyState icon={<Layers />} title="No BOMs yet" description="Create a draft to get started." />
      ) : (
        <TableWrap className="mb-8">
          <Table>
            <THead>
              <TR>
                <TH>Item</TH>
                <TH>Version</TH>
                <TH>Status</TH>
              </TR>
            </THead>
            <TBody>
              {boms.data?.map((bom) => (
                <TR
                  key={bom.id}
                  onClick={() => setSelectedBomId(bom.id)}
                  className={`cursor-pointer ${selectedBomId === bom.id ? "bg-primary/5" : ""}`}
                >
                  <TD className="font-medium">
                    {bom.item_sku} - {bom.item_name}
                  </TD>
                  <TD className="text-muted-foreground">v{bom.version}</TD>
                  <TD>
                    <Badge variant={bomStatusVariant(bom.status)}>{humanize(bom.status)}</Badge>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}

      {bomDetail.data && (
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              {bomDetail.data.item_sku} · v{bomDetail.data.version}
              <Badge variant={bomStatusVariant(bomDetail.data.status)}>
                {humanize(bomDetail.data.status)}
              </Badge>
            </CardTitle>
            {bomDetail.data.status === "draft" && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => activateBom.mutate()}
                loading={activateBom.isPending}
              >
                Activate
              </Button>
            )}
          </CardHeader>
          <CardContent className="space-y-6">
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Component</TH>
                    <TH>Quantity</TH>
                    <TH>UoM</TH>
                  </TR>
                </THead>
                <TBody>
                  {bomDetail.data.lines.map((line) => (
                    <TR key={line.id}>
                      <TD className="font-medium">
                        {line.component_sku} - {line.component_name}
                      </TD>
                      <TD>{line.quantity}</TD>
                      <TD className="text-muted-foreground">{line.unit_of_measure}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>

            {bomDetail.data.status === "draft" && (
              <form onSubmit={onAddLine} className="grid gap-4 sm:grid-cols-[1fr_160px_auto] sm:items-end">
                <Field label="Component" htmlFor="component">
                  <Select
                    id="component"
                    value={lineComponentId}
                    onChange={(e) => setLineComponentId(e.target.value)}
                    required
                  >
                    <option value="" disabled>
                      Select a component
                    </option>
                    {items.data
                      ?.filter((item) => item.id !== bomDetail.data!.item_id)
                      .map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.sku} - {item.name}
                        </option>
                      ))}
                  </Select>
                </Field>
                <Field label="Quantity" htmlFor="qty">
                  <Input
                    id="qty"
                    type="number"
                    step="0.0001"
                    min="0.0001"
                    value={lineQuantity}
                    onChange={(e) => setLineQuantity(e.target.value)}
                    required
                  />
                </Field>
                <Button type="submit" variant="secondary" loading={addLine.isPending}>
                  Add line
                </Button>
              </form>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

import { Archive, CheckCircle2, FilePen, Layers, Trash2 } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { ItemSelect } from "@/components/ItemSelect";
import { humanize, lifecycleStatusVariant } from "@/lib/status";
import { cn } from "@/lib/utils";
import { MAX_LINE_QUANTITY, MAX_SCRAP_PCT } from "./boms/types";
import { BomLineRow } from "./boms/BomLineRow";
import { useBomEditor } from "./boms/useBomEditor";

export function Boms() {
  const {
    selectedBomId,
    setSelectedBomId,
    newBomItemId,
    setNewBomItemId,
    lineComponentId,
    setLineComponentId,
    lineQuantity,
    setLineQuantity,
    lineScrapPct,
    setLineScrapPct,
    lineUnitId,
    setLineUnitId,
    lineUnitQuantity,
    setLineUnitQuantity,
    showNewUnit,
    setShowNewUnit,
    newUnitCode,
    setNewUnitCode,
    newUnitFactor,
    setNewUnitFactor,
    error,
    setError,
    showBulk,
    setShowBulk,
    bulkText,
    setBulkText,
    items,
    componentUnits,
    createUnit,
    boms,
    bomDetail,
    createBom,
    addLine,
    removeLine,
    onLineDrop,
    onMoveLine,
    bulkAddLines,
    activateBom,
    reviseBom,
    deleteBom,
    onCreateBom,
    onAddLine,
  } = useBomEditor();

  return (
    <div>
      <PageHeader
        title="Bills of Materials"
        description="Define the components that go into a manufactured item, then activate a version to use it in planning."
      />

      {boms.data && boms.data.length > 0 && (
        <div className="mb-8 grid grid-cols-2 gap-4 lg:grid-cols-4">
          {[
            { label: "Total BOMs", value: boms.data.length, icon: <Layers />, tone: "primary" as const },
            {
              label: "Active",
              value: boms.data.filter((b) => b.status === "active").length,
              icon: <CheckCircle2 />,
              tone: "success" as const,
            },
            {
              label: "Draft",
              value: boms.data.filter((b) => b.status === "draft").length,
              icon: <FilePen />,
              tone: "warning" as const,
            },
            {
              label: "Archived",
              value: boms.data.filter((b) => b.status === "archived").length,
              icon: <Archive />,
              tone: "terracotta" as const,
            },
          ].map((s) => (
            <StatCard key={s.label} label={s.label} value={s.value} icon={s.icon} tone={s.tone} />
          ))}
        </div>
      )}

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
              <ItemSelect
                id="bom-item"
                items={items.data}
                value={newBomItemId}
                onChange={setNewBomItemId}
                required
              />
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
                  className={cn("cursor-pointer", selectedBomId === bom.id && "bg-primary/5")}
                >
                  <TD className="font-medium">
                    {bom.item_sku} - {bom.item_name}
                  </TD>
                  <TD className="text-muted-foreground">v{bom.version}</TD>
                  <TD>
                    <Badge variant={lifecycleStatusVariant(bom.status)}>{humanize(bom.status)}</Badge>
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
              <Badge variant={lifecycleStatusVariant(bomDetail.data.status)}>
                {humanize(bomDetail.data.status)}
              </Badge>
            </CardTitle>
            {bomDetail.data.status === "draft" && (
              <div className="flex gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-rose-400 hover:text-rose-300"
                  loading={deleteBom.isPending}
                  onClick={() => {
                    if (confirm(`Delete this draft BOM for ${bomDetail.data!.item_sku}? This cannot be undone.`)) {
                      deleteBom.mutate();
                    }
                  }}
                >
                  <Trash2 className="size-4" /> Delete
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => activateBom.mutate()}
                  loading={activateBom.isPending}
                >
                  Activate
                </Button>
              </div>
            )}
            {bomDetail.data.status === "active" && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => reviseBom.mutate()}
                loading={reviseBom.isPending}
              >
                Edit (new version)
              </Button>
            )}
          </CardHeader>
          <CardContent className="space-y-6">
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    {bomDetail.data.status === "draft" && <TH />}
                    <TH>Component</TH>
                    <TH>Quantity</TH>
                    <TH>UoM</TH>
                    <TH>Unit</TH>
                    <TH>Scrap %</TH>
                    {bomDetail.data.status === "draft" && <TH />}
                  </TR>
                </THead>
                <TBody>
                  {bomDetail.data.lines.map((line, index) => (
                    <BomLineRow
                      key={line.id}
                      line={line}
                      depth={0}
                      allBoms={boms.data ?? []}
                      onRemove={bomDetail.data!.status === "draft" ? (id) => removeLine.mutate(id) : undefined}
                      removePending={removeLine.isPending}
                      draggable={bomDetail.data!.status === "draft"}
                      onDropLine={onLineDrop}
                      onMove={onMoveLine}
                      canMoveUp={index > 0}
                      canMoveDown={index < bomDetail.data!.lines.length - 1}
                    />
                  ))}
                </TBody>
              </Table>
            </TableWrap>

            {bomDetail.data.status === "draft" && (
              <>
                <form
                  onSubmit={onAddLine}
                  className="grid gap-4 sm:grid-cols-[1fr_140px_140px_160px_auto] sm:items-end"
                >
                  <Field label="Component" htmlFor="component">
                    <ItemSelect
                      id="component"
                      items={items.data}
                      value={lineComponentId}
                      onChange={(id) => {
                        setLineComponentId(id);
                        setLineUnitId("");
                        setLineUnitQuantity("");
                        setShowNewUnit(false);
                        setNewUnitCode("");
                        setNewUnitFactor("");
                      }}
                      excludeId={bomDetail.data.item_id}
                      required
                    />
                  </Field>
                  <Field label={lineUnitId ? "Quantity (in unit)" : "Quantity"} htmlFor="qty">
                    <Input
                      id="qty"
                      type="number"
                      step="any"
                      min="0.0001"
                      max={lineUnitId ? undefined : MAX_LINE_QUANTITY}
                      value={lineUnitId ? lineUnitQuantity : lineQuantity}
                      onChange={(e) =>
                        lineUnitId ? setLineUnitQuantity(e.target.value) : setLineQuantity(e.target.value)
                      }
                      required
                    />
                  </Field>
                  <Field label="Scrap %" htmlFor="scrap-pct">
                    <Input
                      id="scrap-pct"
                      type="number"
                      step="0.01"
                      min="0"
                      max={MAX_SCRAP_PCT}
                      value={lineScrapPct}
                      onChange={(e) => setLineScrapPct(e.target.value)}
                      title="Expected material loss for this component - MRP orders this much extra"
                    />
                  </Field>
                  <Field label="Unit" htmlFor="line-unit">
                    {showNewUnit ? (
                      <div className="flex gap-1">
                        <Input
                          placeholder="code"
                          value={newUnitCode}
                          onChange={(e) => setNewUnitCode(e.target.value)}
                        />
                        <Input
                          type="number"
                          step="0.000001"
                          min="0.000001"
                          placeholder="factor"
                          value={newUnitFactor}
                          onChange={(e) => setNewUnitFactor(e.target.value)}
                        />
                        <Button
                          type="button"
                          size="icon"
                          variant="secondary"
                          disabled={!newUnitCode || !newUnitFactor}
                          loading={createUnit.isPending}
                          onClick={() => createUnit.mutate()}
                        >
                          <Layers className="size-4" />
                        </Button>
                      </div>
                    ) : (
                      <select
                        id="line-unit"
                        className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                        disabled={!lineComponentId}
                        value={lineUnitId}
                        onChange={(e) => {
                          if (e.target.value === "__new__") {
                            setShowNewUnit(true);
                            setLineUnitId("");
                          } else {
                            setLineUnitId(e.target.value);
                          }
                        }}
                      >
                        <option value="">base unit</option>
                        {componentUnits.data?.map((u) => (
                          <option key={u.id} value={u.id}>
                            {u.code}
                          </option>
                        ))}
                        <option value="__new__">+ New unit...</option>
                      </select>
                    )}
                  </Field>
                  <Button type="submit" variant="secondary" loading={addLine.isPending}>
                    Add line
                  </Button>
                </form>

                <div>
                  <Button type="button" variant="ghost" size="sm" onClick={() => setShowBulk((v) => !v)}>
                    {showBulk ? "Hide" : "Paste from spreadsheet"}
                  </Button>
                  {showBulk && (
                    <div className="mt-2 space-y-2">
                      <textarea
                        className="w-full rounded-md border bg-background p-2 text-sm"
                        rows={4}
                        placeholder={
                          "SKU, quantity, scrap % (optional) - one per line\ne.g.\nWIDGET-1, 4\nWIDGET-2, 2, 5"
                        }
                        value={bulkText}
                        onChange={(e) => setBulkText(e.target.value)}
                      />
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        loading={bulkAddLines.isPending}
                        onClick={() => {
                          setError(null);
                          bulkAddLines.mutate();
                        }}
                      >
                        Import lines
                      </Button>
                    </div>
                  )}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

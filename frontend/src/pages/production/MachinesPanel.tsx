import { FormEvent, useState } from "react";
import { Wrench } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import { humanize, lifecycleStatusVariant } from "@/lib/status";
import { optionalNumber, useWorkCenters, type MachineResponse } from "./setupData";

// Same pattern ReportBuilder.tsx uses for an authenticated file download: open
// the tab synchronously (so the popup blocker doesn't see an async click),
// then point it at the blob once apiFetch's Authorization header has done its
// job - an <img src> can't carry that header on its own.
//
// The backend endpoint returns the bare QR image with no label, so a printed
// sticker with nothing on it but a QR code is a mismatch waiting to happen
// once a shop has more than a couple of machines - this wraps it in a small
// page naming the machine. Built via DOM APIs (not innerHTML/document.write)
// so the machine's own name/code - free text an owner typed - can't inject
// markup into the print tab.
//
// Every failure path reports through onError (the panel's existing Alert)
// rather than returning quietly: there is deliberately no fallback that opens
// the bare unlabelled QR, so a silent return would leave the button looking
// broken with nothing to act on.
async function printQr(
  machineId: string,
  code: string,
  name: string,
  onError: (message: string) => void,
) {
  const tab = window.open("", "_blank");
  if (!tab) {
    // Blocked, so there is nowhere to render the label - don't spend the
    // request. Narrowing here also drops the optional chaining below.
    onError("Your browser blocked the print tab. Allow pop-ups for this site, then try again.");
    return;
  }
  try {
    const res = await apiFetch(`/machines/${machineId}/checkin-qr`);
    if (!res.ok) {
      tab.close();
      onError(`Failed to load the check-in QR for ${code}`);
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const doc = tab.document;
    doc.title = `Check-in QR - ${code}`;
    const style = doc.createElement("style");
    style.textContent = `
      body { font-family: system-ui, sans-serif; text-align: center; padding: 2rem; }
      h1 { font-size: 1.25rem; margin: 0 0 0.25rem; }
      p { color: #555; margin: 0 0 1.5rem; }
      img { width: 320px; height: 320px; }
    `;
    doc.head.appendChild(style);
    const heading = doc.createElement("h1");
    heading.textContent = `${code} - ${name}`;
    const subheading = doc.createElement("p");
    subheading.textContent = "Scan to check in";
    const img = doc.createElement("img");
    img.src = url;
    img.alt = "Check-in QR code";
    doc.body.append(heading, subheading, img);
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch {
    tab.close();
    onError(`Failed to load the check-in QR for ${code}`);
  }
}

export function MachinesPanel() {
  const queryClient = useQueryClient();
  const [workCenterId, setWorkCenterId] = useState("");
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [mtbf, setMtbf] = useState("");
  const [mttr, setMttr] = useState("");
  const [error, setError] = useState<string | null>(null);

  const workCenters = useWorkCenters();
  const machines = useQuery<MachineResponse[]>({
    queryKey: ["machines"],
    queryFn: async () => {
      const res = await apiFetch("/machines");
      if (!res.ok) throw new Error("Failed to load machines");
      return res.json();
    },
  });

  const createMachine = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/machines", {
        method: "POST",
        body: JSON.stringify({
          work_center_id: workCenterId,
          code,
          name,
          mtbf_hours: optionalNumber(mtbf),
          mttr_hours: optionalNumber(mttr),
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create machine");
      }
      return res.json();
    },
    onSuccess: () => {
      setCode("");
      setName("");
      setMtbf("");
      setMttr("");
      queryClient.invalidateQueries({ queryKey: ["machines"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createMachine.mutate();
  }

  const workCenterLabel = (id: string) => {
    const wc = workCenters.data?.find((w) => w.id === id);
    return wc ? `${wc.code} - ${wc.name}` : id;
  };

  return (
    <div>
      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Add a machine</CardTitle>
        </CardHeader>
        <CardContent>
          {workCenters.data && workCenters.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Add a work center first - every machine belongs to one.
            </p>
          ) : (
            <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <Field label="Work center" htmlFor="machine-wc">
                <Select
                  id="machine-wc"
                  value={workCenterId}
                  onChange={(e) => setWorkCenterId(e.target.value)}
                  required
                >
                  <option value="" disabled>
                    Select a work center
                  </option>
                  {workCenters.data?.map((wc) => (
                    <option key={wc.id} value={wc.id}>
                      {wc.code} - {wc.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Code" htmlFor="machine-code">
                <Input id="machine-code" value={code} onChange={(e) => setCode(e.target.value)} required />
              </Field>
              <Field label="Name" htmlFor="machine-name">
                <Input id="machine-name" value={name} onChange={(e) => setName(e.target.value)} required />
              </Field>
              <Field label="MTBF (hours)" htmlFor="machine-mtbf" hint="Optional">
                <Input
                  id="machine-mtbf"
                  type="number"
                  min="0"
                  step="any"
                  value={mtbf}
                  onChange={(e) => setMtbf(e.target.value)}
                />
              </Field>
              <Field label="MTTR (hours)" htmlFor="machine-mttr" hint="Optional">
                <Input
                  id="machine-mttr"
                  type="number"
                  min="0"
                  step="any"
                  value={mttr}
                  onChange={(e) => setMttr(e.target.value)}
                />
              </Field>
              <div className="flex items-end">
                <Button type="submit" className="w-full" loading={createMachine.isPending}>
                  Add machine
                </Button>
              </div>
            </form>
          )}
        </CardContent>
      </Card>

      {machines.data && machines.data.length === 0 ? (
        <EmptyState icon={<Wrench />} title="No machines yet" />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Code</TH>
                <TH>Name</TH>
                <TH>Work center</TH>
                <TH>Status</TH>
                <TH>MTBF (h)</TH>
                <TH>MTTR (h)</TH>
                <TH>Review</TH>
                <TH>QR</TH>
              </TR>
            </THead>
            <TBody>
              {machines.data?.map((m) => (
                <TR key={m.id}>
                  <TD className="font-medium">{m.code}</TD>
                  <TD>{m.name}</TD>
                  <TD className="text-muted-foreground">{workCenterLabel(m.work_center_id)}</TD>
                  <TD>
                    <Badge variant={lifecycleStatusVariant(m.status)}>{humanize(m.status)}</Badge>
                  </TD>
                  <TD className="text-muted-foreground">{m.mtbf_hours ?? "-"}</TD>
                  <TD className="text-muted-foreground">{m.mttr_hours ?? "-"}</TD>
                  <TD>{m.needs_review ? <Badge variant="warning">Needs review</Badge> : ""}</TD>
                  <TD>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setError(null);
                        printQr(m.id, m.code, m.name, setError);
                      }}
                    >
                      Print QR
                    </Button>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}
    </div>
  );
}

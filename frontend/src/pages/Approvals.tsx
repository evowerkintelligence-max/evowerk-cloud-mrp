import { FormEvent, useState } from "react";
import { ClipboardCheck } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";

interface OrgMeResponse {
  id: string;
  name: string;
  role: string;
  adjust_approval_threshold: string;
}

interface AdjustmentRequest {
  id: string;
  item_sku: string;
  location_code: string;
  quantity_delta: string;
  counted_quantity: string | null;
  expected_quantity_at_submit: string;
  reason_code: string;
  reason_note: string | null;
  status: string;
  requested_at: string;
}

export function ApprovalsPanel() {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [threshold, setThreshold] = useState("");
  const [reviewNotes, setReviewNotes] = useState<Record<string, string>>({});

  const org = useQuery<OrgMeResponse>({
    queryKey: ["org-me"],
    queryFn: async () => {
      const res = await apiFetch("/orgs/me");
      if (!res.ok) throw new Error("Failed to load organization");
      return res.json();
    },
  });
  const isOwner = org.data?.role === "owner";

  const requests = useQuery<AdjustmentRequest[]>({
    queryKey: ["inventory-adjustment-requests"],
    queryFn: async () => {
      const res = await apiFetch("/inventory/adjustment-requests?status=pending");
      if (!res.ok) throw new Error("Failed to load pending approvals");
      return res.json();
    },
  });

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ["inventory-adjustment-requests"] });
    queryClient.invalidateQueries({ queryKey: ["inventory"] });
  }

  const approve = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/inventory/adjustment-requests/${id}/approve`, {
        method: "POST",
        body: JSON.stringify({ review_note: reviewNotes[id] || null }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b.detail ?? "Approve failed");
      }
      return res.json();
    },
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  });

  const reject = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiFetch(`/inventory/adjustment-requests/${id}/reject`, {
        method: "POST",
        body: JSON.stringify({ review_note: reviewNotes[id] || null }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b.detail ?? "Reject failed");
      }
      return res.json();
    },
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  });

  const updateThreshold = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/orgs/me", {
        method: "PATCH",
        body: JSON.stringify({ adjust_approval_threshold: threshold }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b.detail ?? "Failed to update threshold");
      }
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["org-me"] });
      setThreshold("");
    },
    onError: (e: Error) => setError(e.message),
  });

  function onThresholdSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    updateThreshold.mutate();
  }

  return (
    <div>
      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      {isOwner && (
        <Card className="mb-8">
          <CardHeader>
            <CardTitle>Approval threshold</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={onThresholdSubmit} className="flex items-end gap-4">
              <Field
                label="Quantity"
                htmlFor="threshold"
                hint={`Current: ${org.data?.adjust_approval_threshold ?? "—"}. Manager adjustments/counts above this need approval.`}
              >
                <Input
                  id="threshold"
                  type="number"
                  step="0.0001"
                  min="0"
                  value={threshold}
                  onChange={(e) => setThreshold(e.target.value)}
                  placeholder={org.data?.adjust_approval_threshold}
                />
              </Field>
              <Button type="submit" loading={updateThreshold.isPending}>
                Save
              </Button>
            </form>
          </CardContent>
        </Card>
      )}

      {requests.data && requests.data.length === 0 ? (
        <EmptyState icon={<ClipboardCheck />} title="No pending approvals" />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Item</TH>
                <TH>Location</TH>
                <TH>Delta</TH>
                <TH>Counted / Expected</TH>
                <TH>Reason</TH>
                <TH>Requested</TH>
                {isOwner && <TH>Actions</TH>}
              </TR>
            </THead>
            <TBody>
              {requests.data?.map((req) => (
                <TR key={req.id}>
                  <TD className="font-medium">{req.item_sku}</TD>
                  <TD className="text-muted-foreground">{req.location_code}</TD>
                  <TD>{req.quantity_delta}</TD>
                  <TD className="text-muted-foreground">
                    {req.counted_quantity != null
                      ? `${req.counted_quantity} / ${req.expected_quantity_at_submit}`
                      : "—"}
                  </TD>
                  <TD className="text-muted-foreground">
                    {req.reason_code}
                    {req.reason_note ? `: ${req.reason_note}` : ""}
                  </TD>
                  <TD className="text-muted-foreground">
                    {new Date(req.requested_at).toLocaleString()}
                  </TD>
                  {isOwner && (
                    <TD>
                      <div className="flex items-center gap-2">
                        <Input
                          placeholder="Note (optional)"
                          className="h-8 w-36 text-xs"
                          value={reviewNotes[req.id] ?? ""}
                          onChange={(e) =>
                            setReviewNotes((prev) => ({ ...prev, [req.id]: e.target.value }))
                          }
                        />
                        <Button
                          size="sm"
                          variant="secondary"
                          loading={approve.isPending}
                          onClick={() => approve.mutate(req.id)}
                        >
                          Approve
                        </Button>
                        <Button
                          size="sm"
                          variant="destructive"
                          loading={reject.isPending}
                          onClick={() => reject.mutate(req.id)}
                        >
                          Reject
                        </Button>
                      </div>
                    </TD>
                  )}
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}
    </div>
  );
}

import type { UseMutationResult, UseQueryResult } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import type { PurchaseOrderDetailResponse } from "./types";

export function PoConfirmDialogs({
  poDetail,
  confirmSubmit,
  setConfirmSubmit,
  submitPo,
  confirmCancel,
  setConfirmCancel,
  cancelPo,
}: {
  poDetail: UseQueryResult<PurchaseOrderDetailResponse>;
  confirmSubmit: boolean;
  setConfirmSubmit: (open: boolean) => void;
  submitPo: UseMutationResult<unknown, Error, void>;
  confirmCancel: boolean;
  setConfirmCancel: (open: boolean) => void;
  cancelPo: UseMutationResult<unknown, Error, void>;
}) {
  return (
    <>
      {poDetail.data && (
        <Dialog open={confirmSubmit} onClose={() => setConfirmSubmit(false)} title="Submit purchase order?">
          <p className="text-sm text-muted-foreground">
            PO-{poDetail.data.po_number} will move to <strong>Submitted</strong> and its lines can no
            longer be edited. If the supplier has an email on file, we'll send them the PO.
          </p>
          <div className="flex justify-end gap-2 pt-4">
            <Button variant="secondary" onClick={() => setConfirmSubmit(false)}>
              Never mind
            </Button>
            <Button
              loading={submitPo.isPending}
              onClick={() => {
                submitPo.mutate(undefined, { onSuccess: () => setConfirmSubmit(false) });
              }}
            >
              Submit
            </Button>
          </div>
        </Dialog>
      )}

      {poDetail.data && (
        <Dialog open={confirmCancel} onClose={() => setConfirmCancel(false)} title="Cancel purchase order?">
          <p className="text-sm text-muted-foreground">
            PO-{poDetail.data.po_number} will be cancelled. This can't be undone.
          </p>
          <div className="flex justify-end gap-2 pt-4">
            <Button variant="secondary" onClick={() => setConfirmCancel(false)}>
              Never mind
            </Button>
            <Button
              variant="destructive"
              loading={cancelPo.isPending}
              onClick={() => {
                cancelPo.mutate(undefined, { onSuccess: () => setConfirmCancel(false) });
              }}
            >
              Cancel PO
            </Button>
          </div>
        </Dialog>
      )}
    </>
  );
}

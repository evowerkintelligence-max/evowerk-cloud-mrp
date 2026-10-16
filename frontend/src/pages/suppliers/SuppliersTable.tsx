import { Truck } from "lucide-react";
import type { UseMutationResult, UseQueryResult } from "@tanstack/react-query";
import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";
import type { SupplierResponse } from "./types";

export function SuppliersTable({
  suppliers,
  openEdit,
  setSupplierActive,
}: {
  suppliers: UseQueryResult<SupplierResponse[]>;
  openEdit: (supplier: SupplierResponse) => void;
  setSupplierActive: UseMutationResult<unknown, Error, { id: string; active: boolean }>;
}) {
  return (
    <>
      {suppliers.data && suppliers.data.length === 0 ? (
        <EmptyState
          icon={<Truck />}
          title="No suppliers yet"
          description="Add a vendor to start creating purchase orders."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Email</TH>
                <TH>Phone</TH>
                <TH>Status</TH>
                <TH></TH>
              </TR>
            </THead>
            <TBody>
              {suppliers.data?.map((supplier) => (
                <TR key={supplier.id}>
                  <TD className="font-medium">{supplier.name}</TD>
                  <TD className="text-muted-foreground">{supplier.email ?? "—"}</TD>
                  <TD className="text-muted-foreground">{supplier.phone ?? "—"}</TD>
                  <TD>
                    <Badge variant={supplier.is_active ? "success" : "neutral"}>
                      {supplier.is_active ? "Active" : "Inactive"}
                    </Badge>
                  </TD>
                  <TD className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button variant="outline" size="sm" onClick={() => openEdit(supplier)}>
                        Edit
                      </Button>
                      <Button
                        variant={supplier.is_active ? "secondary" : "outline"}
                        size="sm"
                        loading={
                          setSupplierActive.isPending && setSupplierActive.variables?.id === supplier.id
                        }
                        onClick={() =>
                          setSupplierActive.mutate({ id: supplier.id, active: !supplier.is_active })
                        }
                      >
                        {supplier.is_active ? "Deactivate" : "Reactivate"}
                      </Button>
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}
    </>
  );
}

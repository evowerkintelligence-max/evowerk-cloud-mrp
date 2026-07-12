import { FormEvent, useState } from "react";
import { MapPin } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";

interface LocationResponse {
  id: string;
  code: string;
  name: string;
  is_default: boolean;
}

export function LocationsPanel() {
  const queryClient = useQueryClient();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const locations = useQuery<LocationResponse[]>({
    queryKey: ["locations"],
    queryFn: async () => {
      const res = await apiFetch("/locations");
      if (!res.ok) throw new Error("Failed to load locations");
      return res.json();
    },
  });

  const createLocation = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/locations", {
        method: "POST",
        body: JSON.stringify({ code, name }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create location");
      }
      return res.json();
    },
    onSuccess: () => {
      setCode("");
      setName("");
      queryClient.invalidateQueries({ queryKey: ["locations"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createLocation.mutate();
  }

  return (
    <div>
      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Add a location</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Code" htmlFor="code">
              <Input id="code" value={code} onChange={(e) => setCode(e.target.value)} required />
            </Field>
            <Field label="Name" htmlFor="name">
              <Input id="name" value={name} onChange={(e) => setName(e.target.value)} required />
            </Field>
            <div className="flex items-end">
              <Button type="submit" className="w-full" loading={createLocation.isPending}>
                Add location
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {locations.data && locations.data.length === 0 ? (
        <EmptyState icon={<MapPin />} title="No locations yet" />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Code</TH>
                <TH>Name</TH>
                <TH>Default</TH>
              </TR>
            </THead>
            <TBody>
              {locations.data?.map((loc) => (
                <TR key={loc.id}>
                  <TD className="font-medium">{loc.code}</TD>
                  <TD>{loc.name}</TD>
                  <TD>{loc.is_default ? <Badge variant="success">Default</Badge> : ""}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}
    </div>
  );
}

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LayoutGrid, Plus } from "lucide-react";
import { apiFetch } from "../../api/client";
import { PageHeader } from "@/components/PageHeader";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

interface Dashboard {
  id: string;
  name: string;
  created_at: string;
}

export function Dashboards() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [name, setName] = useState("");

  const dashboards = useQuery<Dashboard[]>({
    queryKey: ["bi-dashboards"],
    queryFn: async () => {
      const res = await apiFetch("/bi/dashboards");
      if (!res.ok) throw new Error("Failed to load dashboards");
      return res.json();
    },
  });

  const create = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/bi/dashboards", { method: "POST", body: JSON.stringify({ name: name.trim() }) });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to create dashboard");
      return res.json() as Promise<Dashboard>;
    },
    onSuccess: async (d) => {
      await qc.invalidateQueries({ queryKey: ["bi-dashboards"] });
      navigate(`/bi/${d.id}`);
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="BI Dashboards"
        description="Saved charts, grouped into dashboards."
        actions={
          <Link to="/bi/new">
            <Button size="sm">
              <Plus /> New chart
            </Button>
          </Link>
        }
      />

      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 p-5">
          <Field label="New dashboard name" className="min-w-[16rem]">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Ops overview" />
          </Field>
          <Button disabled={!name.trim()} loading={create.isPending} onClick={() => create.mutate()}>
            Create dashboard
          </Button>
          {create.isError && <Alert variant="destructive">{(create.error as Error).message}</Alert>}
        </CardContent>
      </Card>

      {dashboards.isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : !dashboards.data?.length ? (
        <Alert>No dashboards yet — create one above, or save a chart to one from the chart builder.</Alert>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {dashboards.data.map((d) => (
            <Link key={d.id} to={`/bi/${d.id}`} className="group rounded-lg focus-visible:outline-none">
              <Card className="h-full transition-colors hover:border-primary/40 group-focus-visible:ring-2 group-focus-visible:ring-ring">
                <CardContent className="flex items-center gap-4 p-5">
                  <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary [&_svg]:size-5">
                    <LayoutGrid />
                  </div>
                  <CardTitle className="text-base">{d.name}</CardTitle>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

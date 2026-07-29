// frontend/src/pages/CheckIn.tsx
import { FormEvent, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { AuthLayout } from "@/components/AuthLayout";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";

interface RosterOperator {
  id: string;
  name: string;
}

interface RosterResponse {
  machine_id: string;
  machine_name: string;
  operators: RosterOperator[];
}

interface CaptureResponse {
  operator_id: string;
  operator_name: string;
}

function rememberedKey(machineId: string) {
  return `evowerk:checkin:${machineId}`;
}

function readRemembered(machineId: string): RosterOperator | null {
  try {
    const raw = localStorage.getItem(rememberedKey(machineId));
    return raw ? (JSON.parse(raw) as RosterOperator) : null;
  } catch {
    return null;
  }
}

export function CheckIn() {
  const { machineId = "" } = useParams();
  const remembered = readRemembered(machineId);
  const [showList, setShowList] = useState(!remembered);
  const [newName, setNewName] = useState("");
  const [confirmed, setConfirmed] = useState<string | null>(null);

  const roster = useQuery<RosterResponse>({
    queryKey: ["checkin-roster", machineId],
    queryFn: async () => {
      const res = await apiFetch(`/checkin/${machineId}`);
      if (!res.ok) throw new Error("This QR code isn't linked to a machine anymore.");
      return res.json();
    },
  });

  const checkIn = useMutation({
    mutationFn: async (body: { operator_id?: string; name?: string }) => {
      const res = await apiFetch(`/checkin/${machineId}`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error("Check-in failed. Try again.");
      return res.json() as Promise<CaptureResponse>;
    },
    onSuccess: (data) => {
      localStorage.setItem(
        rememberedKey(machineId),
        JSON.stringify({ id: data.operator_id, name: data.operator_name }),
      );
      setConfirmed(data.operator_name);
    },
  });

  function onAddName(e: FormEvent) {
    e.preventDefault();
    if (newName.trim()) checkIn.mutate({ name: newName.trim() });
  }

  if (confirmed) {
    return (
      <AuthLayout title="You're checked in" subtitle={roster.data?.machine_name}>
        <Alert variant="success">Checked in as {confirmed}. You're all set.</Alert>
        <Button className="mt-4 w-full" variant="secondary" onClick={() => setConfirmed(null)}>
          Not you? Check in again
        </Button>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title={roster.data ? `Check in at ${roster.data.machine_name}` : "Check in"}
      subtitle="Tap your name to start this machine under your name."
    >
      {roster.isError && <Alert variant="destructive">{(roster.error as Error).message}</Alert>}
      {checkIn.isError && (
        <Alert variant="destructive" className="mb-4">
          {(checkIn.error as Error).message}
        </Alert>
      )}

      {remembered && !showList && (
        <div className="space-y-3">
          <Button
            className="w-full"
            loading={checkIn.isPending}
            onClick={() => checkIn.mutate({ operator_id: remembered.id })}
          >
            Continue as {remembered.name}
          </Button>
          <button
            type="button"
            onClick={() => setShowList(true)}
            className="block w-full text-center text-xs font-medium text-primary hover:underline"
          >
            Not you? Choose from the list
          </button>
        </div>
      )}

      {(!remembered || showList) && (
        <div className="space-y-4">
          <div className="space-y-2">
            {roster.data?.operators.map((o) => (
              <Button
                key={o.id}
                variant="outline"
                className="w-full justify-start"
                loading={checkIn.isPending}
                onClick={() => checkIn.mutate({ operator_id: o.id })}
              >
                {o.name}
              </Button>
            ))}
            {roster.data && roster.data.operators.length === 0 && (
              <p className="text-sm text-muted-foreground">No one on the roster yet - add your name below.</p>
            )}
          </div>
          <form onSubmit={onAddName} className="flex gap-2">
            <Field label="Not on the list?" htmlFor="checkin-new-name" className="flex-1">
              <Input
                id="checkin-new-name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="Type your name"
              />
            </Field>
            <Button type="submit" variant="secondary" loading={checkIn.isPending} className="self-end">
              Add
            </Button>
          </form>
        </div>
      )}
    </AuthLayout>
  );
}

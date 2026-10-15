import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";

export interface WorkCenterResponse {
  id: string;
  code: string;
  name: string;
  status: string;
  machine_capacity: number;
  default_cycle_time_seconds: number | null;
  default_mtbf_hours: number | null;
  default_mttr_hours: number | null;
}

export interface MachineResponse {
  id: string;
  work_center_id: string;
  code: string;
  name: string;
  status: string;
  mtbf_hours: number | null;
  mttr_hours: number | null;
  needs_review: boolean;
}

export interface OperatorResponse {
  id: string;
  name: string;
  code: string | null;
  badge_id: string | null;
  status: string;
  needs_review: boolean;
  duplicate_of_id: string | null;
  duplicate_of_name: string | null;
}

export interface RoutingStepResponse {
  id: string;
  sequence: number;
  work_center_id: string;
  cycle_time_seconds: number | null;
  setup_time_seconds: number | null;
}

export interface RoutingResponse {
  id: string;
  item_id: string;
  version: number;
  status: string;
  steps: RoutingStepResponse[];
}

export interface ItemResponse {
  id: string;
  sku: string;
  name: string;
}

/** Empty string -> null, so an untouched optional number field isn't sent as 0. */
export function optionalNumber(value: string): number | null {
  return value.trim() === "" ? null : Number(value);
}

export function useWorkCenters() {
  return useQuery<WorkCenterResponse[]>({
    queryKey: ["work-centers"],
    queryFn: async () => {
      const res = await apiFetch("/work-centers");
      if (!res.ok) throw new Error("Failed to load work centers");
      return res.json();
    },
  });
}

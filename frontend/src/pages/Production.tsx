import { PageHeader } from "@/components/PageHeader";
import { JobsPanel } from "./production/JobsPanel";

export function Production() {
  return (
    <div>
      <PageHeader
        title="Production"
        description="The jobs on the floor: what is scheduled, what is running, and what was made."
      />
      <JobsPanel />
    </div>
  );
}

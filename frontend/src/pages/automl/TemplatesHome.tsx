import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { TemplatePicker } from "./templates/TemplatePicker";
import { MappingWizard } from "./templates/MappingWizard";
import type { AutoMLTemplateSummary } from "./api";

/** Default AutoML landing page: Step 1 pick a template, Step 2 map columns
 * and run (MappingWizard), Step 3 view results (navigates to ResultsView at
 * /automl/runs/:id once the run is created). */
export function TemplatesHome() {
  const navigate = useNavigate();
  const [template, setTemplate] = useState<AutoMLTemplateSummary | null>(null);

  return (
    <div className="space-y-6">
      <PageHeader
        title="AutoML"
        description="Pick a guided template, map your columns, and let AutoML train and explain a model for you."
        actions={
          <Button variant="secondary" onClick={() => navigate("/automl/advanced")}>
            Advanced / Custom
          </Button>
        }
      />

      {!template ? (
        <TemplatePicker onSelect={setTemplate} />
      ) : (
        <MappingWizard
          template={template}
          onBack={() => setTemplate(null)}
          onCreated={(runId) => navigate(`/automl/runs/${runId}`)}
        />
      )}
    </div>
  );
}

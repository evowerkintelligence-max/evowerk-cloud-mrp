import { useState } from "react";
import { CaseDetail } from "./rca-cases/CaseDetail";
import { CaseList } from "./rca-cases/CaseList";

export function RcaCases() {
  const [openCaseId, setOpenCaseId] = useState<string | null>(null);
  return openCaseId ? (
    <CaseDetail caseId={openCaseId} onBack={() => setOpenCaseId(null)} />
  ) : (
    <CaseList onOpen={setOpenCaseId} />
  );
}

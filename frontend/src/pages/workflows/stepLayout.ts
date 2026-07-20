export interface LayoutStep {
  id: string;
  depends_on: string[];
}

const COLUMN_WIDTH = 260;
const ROW_HEIGHT = 120;

// Layered auto-layout: a step with no unresolved dependency goes in column 0,
// each subsequent column holds steps whose dependencies are all already
// placed in an earlier column - the same grouping workflow_dag.topo_layers
// computes server-side (Kahn's algorithm), ported client-side so the canvas
// can lay out a workflow saved before per-step positions existed.
export function autoLayout<T extends LayoutStep>(steps: T[]): Record<string, { x: number; y: number }> {
  const byId = new Map(steps.map((s) => [s.id, s]));
  const columnOf = new Map<string, number>();

  function columnFor(id: string, seen: Set<string>): number {
    if (columnOf.has(id)) return columnOf.get(id)!;
    if (seen.has(id)) return 0; // cycle - server-side validation rejects it, just don't infinite-loop here
    seen.add(id);
    const deps = byId.get(id)?.depends_on ?? [];
    const col = deps.length === 0 ? 0 : 1 + Math.max(...deps.map((d) => (byId.has(d) ? columnFor(d, seen) : 0)));
    columnOf.set(id, col);
    return col;
  }

  const rowInColumn: Record<number, number> = {};
  const positions: Record<string, { x: number; y: number }> = {};
  for (const step of steps) {
    const col = columnFor(step.id, new Set());
    const row = rowInColumn[col] ?? 0;
    rowInColumn[col] = row + 1;
    positions[step.id] = { x: col * COLUMN_WIDTH, y: row * ROW_HEIGHT };
  }
  return positions;
}

// True if sourceId already (transitively) depends on targetId - i.e. adding
// "targetId depends_on sourceId" would close a loop back to targetId.
export function wouldCreateCycle(steps: LayoutStep[], targetId: string, sourceId: string): boolean {
  const byId = new Map(steps.map((s) => [s.id, s]));
  const seen = new Set<string>();
  function dependsOn(fromId: string, needle: string): boolean {
    if (seen.has(fromId)) return false;
    seen.add(fromId);
    const deps = byId.get(fromId)?.depends_on ?? [];
    return deps.some((depId) => depId === needle || dependsOn(depId, needle));
  }
  return dependsOn(sourceId, targetId);
}

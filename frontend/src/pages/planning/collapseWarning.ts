// Framework-free decision for PlannedQtyInput: whether changing a product's
// quantity is about to silently collapse an imported multi-date plan into a
// single dated row. Pulled out (same convention as saveScheduler.ts,
// orderResult.ts, csvImport.ts) so the rule is testable without rendering.
export interface CollapseWarningInputs {
  /** How many separate dated rows make up the product's current plan. */
  plannedBucketCount: number;
  /** Whether the quantity the user is about to save differs from what is planned. */
  quantityChanged: boolean;
  /** Whether the user already confirmed the collapse for this edit. */
  alreadyConfirmed: boolean;
}

/** True when saving now would replace more than one planned date with a
 * single date, and the user has not already agreed to that. */
export function shouldWarnBeforeCollapse(inputs: CollapseWarningInputs): boolean {
  return inputs.plannedBucketCount > 1 && inputs.quantityChanged && !inputs.alreadyConfirmed;
}

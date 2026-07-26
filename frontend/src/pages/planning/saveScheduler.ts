// Framework-free debounce-and-save scheduler. Pulled out of PlannedQtyInput
// so the timer/re-arm/flush/cancel behaviour can be driven directly by
// vitest with fake timers, instead of only being reachable through a
// rendered React component.
export interface SaveScheduler<T> {
  /** Arms (or re-arms) the debounce timer, carrying the latest payload. */
  schedule: (payload: T) => void;
  /** Saves immediately if a save is pending, and cancels the timer so it
   * cannot also fire later and save a second time. No-op if nothing pending. */
  flush: () => void;
  /** Discards the pending save without calling save. */
  cancel: () => void;
  /** Whether a save is currently armed (debounce running, not yet fired). */
  isPending: () => boolean;
}

export function createSaveScheduler<T>(
  save: (payload: T) => void,
  delayMs: number,
): SaveScheduler<T> {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let pendingPayload: T | undefined;

  function fire() {
    timer = null;
    const payload = pendingPayload as T;
    pendingPayload = undefined;
    save(payload);
  }

  function schedule(payload: T) {
    if (timer !== null) clearTimeout(timer);
    pendingPayload = payload;
    timer = setTimeout(fire, delayMs);
  }

  function flush() {
    if (timer === null) return;
    clearTimeout(timer);
    fire();
  }

  function cancel() {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    pendingPayload = undefined;
  }

  function isPending() {
    return timer !== null;
  }

  return { schedule, flush, cancel, isPending };
}

/** DOM work only: network completion and rendering completion are different boundaries. */
export type WebTranslationCommitTask = {
  key: string;
  scopeId?: string;
  /** Read current visibility at selection time; omitted priorities preserve FIFO. */
  priority?: () => 'visible' | 'background';
  /** Recheck epoch, scope generation and source identity immediately before commit. */
  isValid: () => boolean;
  /** Read the latest mode here; do not capture the enqueue-time display mode. */
  commit: () => void;
};
export type WebTranslationCommitOutcome = 'committed' | 'stale' | 'cancelled' | 'replaced' | 'rejected' | 'error';
export type WebTranslationCommitQueueOptions = {
  maxPending?: number;
  maxItemsPerSlice?: number;
  maxSliceMs?: number;
  /** A scheduled callback must be asynchronous. Return its cancellation function. */
  schedule?: (callback: () => void) => () => void;
  now?: () => number;
  /** Isolated hooks around each slice, for batched layout capture and restoration. */
  beforeSlice?: () => void;
  afterSlice?: () => void;
  onSettled?: (task: WebTranslationCommitTask, outcome: WebTranslationCommitOutcome, error?: unknown) => void;
};

export function createWebTranslationCommitQueue(options: WebTranslationCommitQueueOptions = {}) {
  const positive = (value: number | undefined, fallback: number, ceiling: number) =>
    Number.isFinite(value) && value! > 0 ? Math.min(ceiling, Math.max(1, Math.floor(value!))) : fallback;
  const maxPending = positive(options.maxPending, 2048, 8192);
  const maxItems = positive(options.maxItemsPerSlice, 4, 32);
  const maxSliceMs = positive(options.maxSliceMs, 8, 32);
  const now = options.now ?? (() => performance.now());
  const schedule = options.schedule ?? ((callback: () => void) => {
    const timer = setTimeout(callback, 0);
    return () => clearTimeout(timer);
  });
  const pending = new Map<string, WebTranslationCommitTask>();
  let cancelScheduled: (() => void) | undefined;
  let flushing = false;
  let disposed = false;
  let generation = 0;
  // Bound geometry reads independently of queue length. Every fourth selection
  // takes the oldest item, even when visible results continue arriving.
  const priorityLookahead = 32;
  let priorityBypasses = 0;
  function selectTask() {
    const oldest = pending.values().next().value as WebTranslationCommitTask | undefined;
    if (!oldest) return undefined;
    if (priorityBypasses >= 3) { priorityBypasses = 0; return oldest; }
    let inspected = 0;
    for (const task of pending.values()) {
      if (inspected++ >= priorityLookahead) break;
      let visible = false;
      try { visible = task.priority?.() === 'visible'; } catch { /* Treat failed reads as background. */ }
      if (visible) {
        priorityBypasses = task === oldest ? 0 : priorityBypasses + 1;
        return task;
      }
    }
    priorityBypasses = 0;
    return oldest;
  }
  function runHook(hook: (() => void) | undefined) {
    try { hook?.(); } catch { /* Layout observers cannot strand accepted results. */ }
  }
  function settle(task: WebTranslationCommitTask, outcome: WebTranslationCommitOutcome, error?: unknown) {
    try { options.onSettled?.(task, outcome, error); } catch { /* One observer cannot strand other accepted results. */ }
  }
  function ensureScheduled() {
    if (!disposed && !flushing && !cancelScheduled && pending.size) cancelScheduled = schedule(flush);
  }
  function flush() {
    cancelScheduled = undefined;
    if (disposed) return;
    flushing = true;
    const currentGeneration = generation;
    const started = now();
    let processed = 0;
    try {
      runHook(options.beforeSlice);
      while (pending.size && generation === currentGeneration && !disposed && processed < maxItems) {
        if (processed && now() - started >= maxSliceMs) break;
        const task = selectTask();
        if (!task || generation !== currentGeneration || disposed) break;
        if (pending.get(task.key) !== task) break;
        pending.delete(task.key);
        processed += 1;
        try {
          if (!task.isValid() || generation !== currentGeneration || disposed) settle(task, 'stale');
          else { task.commit(); settle(task, 'committed'); }
        } catch (error) { settle(task, 'error', error); }
      }
    } finally {
      runHook(options.afterSlice);
      flushing = false;
      ensureScheduled();
    }
  }
  function enqueue(task: WebTranslationCommitTask) {
    if (disposed || (!pending.has(task.key) && pending.size >= maxPending)) {
      settle(task, 'rejected');
      return false;
    }
    const previous = pending.get(task.key);
    // Map.set keeps the original FIFO slot while replacing only the result/validity closure.
    pending.set(task.key, task);
    if (previous) settle(previous, 'replaced');
    ensureScheduled();
    return true;
  }
  function cancel(key: string) {
    const task = pending.get(key);
    if (!task) return;
    pending.delete(key);
    settle(task, 'cancelled');
    if (!pending.size) { cancelScheduled?.(); cancelScheduled = undefined; }
  }
  function reset() {
    generation += 1;
    priorityBypasses = 0;
    cancelScheduled?.(); cancelScheduled = undefined;
    const removed = [...pending.values()];
    pending.clear();
    for (const task of removed) settle(task, 'cancelled');
    ensureScheduled();
  }
  return {
    enqueue,
    cancel,
    cancelWhere(predicate: (task: WebTranslationCommitTask) => boolean) {
      for (const task of [...pending.values()]) if (predicate(task)) cancel(task.key);
    },
    reset,
    dispose() { disposed = true; reset(); },
    get size() { return pending.size; },
  };
}

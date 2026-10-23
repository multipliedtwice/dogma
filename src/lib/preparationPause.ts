import type {Generation, Health} from './types';

type Snapshot = {generation: Pick<Generation, 'id' | 'status'>; health: Pick<Health, 'active'>};
type Dependencies = {
  pause: (id: string, signal: AbortSignal) => Promise<unknown>;
  refresh: (id: string, signal: AbortSignal) => Promise<Snapshot>;
  pending: (id: string | null) => void;
  refreshError: (reason: unknown) => void;
};
type Request = {id: string; controller?: AbortController; timer?: ReturnType<typeof setTimeout>};
const settled = (status: string) => ['paused', 'failed', 'ready', 'review', 'held'].includes(status);

export function healthSettling(generation: Pick<Generation, 'id' | 'status'> | null, health: Pick<Health, 'active'> | null): boolean {
  return Boolean(generation && health?.active === generation.id && settled(generation.status));
}

// Acceptance only sets the stop flag. Keep the request until both views confirm cleanup.
export function createPreparationPause(dependencies: Dependencies, timeoutMs = 5000, intervalMs = 1000) {
  let current: Request | null = null;

  function finish(request: Request, notify = true): void {
    if (current !== request) return;
    current = null;
    clearTimeout(request.timer);
    request.controller?.abort();
    if (notify) dependencies.pending(null);
  }

  async function bounded<T>(request: Request, operation: (signal: AbortSignal) => Promise<T>): Promise<T> {
    const controller = new AbortController();
    request.controller = controller;
    let rejectAbort: () => void = () => {};
    const aborted = new Promise<never>((_, reject) => {
      rejectAbort = () => reject(new Error('Pause confirmation was interrupted or timed out.'));
      controller.signal.addEventListener('abort', rejectAbort, {once: true});
    });
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await Promise.race([operation(controller.signal), aborted]);
    } finally {
      clearTimeout(timer);
      controller.signal.removeEventListener('abort', rejectAbort);
      controller.abort(); // Also cancel a still-pending sibling of a rejected request pair.
      if (request.controller === controller) request.controller = undefined;
    }
  }

  async function poll(request: Request): Promise<void> {
    try {
      const next = await bounded(request, signal => dependencies.refresh(request.id, signal));
      if (current !== request) return;
      if (next.generation.id !== request.id) throw new Error('Pause confirmation returned a different generation.');
      if (settled(next.generation.status) && next.health.active !== request.id) {
        finish(request);
      }
    } catch (reason) {
      if (current === request) dependencies.refreshError(reason);
    } finally {
      // Serial, cancellable polling: at most one timeout-bounded request pair is in flight.
      if (current === request) request.timer = setTimeout(() => { void poll(request); }, intervalMs);
    }
  }

  return {
    async request(id: string): Promise<boolean> {
      if (current) return false;
      const request: Request = {id};
      current = request;
      dependencies.pending(id);
      try {
        await bounded(request, signal => dependencies.pause(id, signal));
        if (current !== request) return false;
        void poll(request); // Refresh immediately after acceptance, not after the ordinary poll interval.
        return true;
      } catch (reason) {
        if (current !== request) return false;
        finish(request);
        throw reason;
      }
    },
    // Only after a successful backend deletion: an absent worker cannot be
    // confirmed by polling a generation that no longer exists.
    removed(id: string): void {
      if (current?.id === id) finish(current);
    },
    dispose(): void {
      if (current) finish(current, false);
    },
  };
}

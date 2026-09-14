import { localizedMessage } from '../../i18n/message';
import { ConnectionError } from './errors';
import type { ConnectionPhase, ConnectionState, ReaderDiscoveryResult, ActiveReaderRuntime } from './types';

export type ConnectionOperation = {
  generation: number;
  signal: AbortSignal;
};

type StateListener = (state: ConnectionState) => void;

/**
 * Small cancellable state machine used by forms and lifecycle tests.  Runtime
 * activation remains atomic in runtime.ts; this class owns only UI operation
 * generations and stale-result rejection.
 */
export class ConnectionMachine {
  private stateValue: ConnectionState = {
    active: null,
    candidate: null,
    error: null,
    generation: 0,
    phase: 'idle',
  };
  private operation: { controller: AbortController; generation: number } | null = null;
  private listeners = new Set<StateListener>();

  get state(): ConnectionState {
    return this.stateValue;
  }

  subscribe(listener: StateListener): () => void {
    this.listeners.add(listener);
    listener(this.stateValue);
    return () => this.listeners.delete(listener);
  }

  begin(phase: Extract<ConnectionPhase, 'restoring' | 'discovering' | 'activating'>): ConnectionOperation {
    this.operation?.controller.abort();
    const controller = new AbortController();
    const generation = this.stateValue.generation + 1;
    this.operation = { controller, generation };
    this.update({ candidate: null, error: null, generation, phase });
    return { generation, signal: controller.signal };
  }

  setCandidate(candidate: ReaderDiscoveryResult): void {
    this.update({
      candidate,
      phase: candidate.requires_confirmation ? 'awaiting_redirect_confirmation' : 'activating',
    });
  }

  isCurrent(operation: ConnectionOperation): boolean {
    return this.operation?.generation === operation.generation && !operation.signal.aborted;
  }

  complete(runtime: ActiveReaderRuntime): void {
    this.assertCurrent();
    this.operation = null;
    this.update({ active: runtime, candidate: null, error: null, phase: 'active' });
  }

  fail(error: ConnectionError): void {
    this.assertCurrent();
    this.operation = null;
    this.update({ error, phase: 'error' });
  }

  cancel(): void {
    this.operation?.controller.abort();
    this.operation = null;
    this.update({ candidate: null, error: null, phase: this.stateValue.active ? 'active' : 'idle' });
  }

  private assertCurrent(): void {
    if (!this.operation) throw new ConnectionError('stale_runtime', localizedMessage('errors:connectionOperationExpired'));
  }

  private update(partial: Partial<ConnectionState>): void {
    this.stateValue = { ...this.stateValue, ...partial };
    for (const listener of [...this.listeners]) listener(this.stateValue);
  }
}

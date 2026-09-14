import {
  getActiveRuntimeSnapshot,
  getConnectionGenerationSnapshot,
  getReaderSessionEpochSnapshot,
} from './snapshot';
import type { ActiveReaderRuntime } from './types';

/** Immutable context captured when a request or mutation starts. */
export type ReaderRuntimeContext = {
  generation: number;
  runtime: ActiveReaderRuntime;
  serverId: string;
  sessionEpoch: number;
};

/** Capture the active server and generation for one async operation. */
export function captureRuntimeContext(
  runtime: ActiveReaderRuntime | null = getActiveRuntimeSnapshot(),
): ReaderRuntimeContext | null {
  if (!runtime) return null;
  const generation = getConnectionGenerationSnapshot();
  if (getActiveRuntimeSnapshot() !== runtime || generation !== runtime.generation) return null;
  return {
    generation,
    runtime,
    serverId: runtime.identity.server_id,
    sessionEpoch: getReaderSessionEpochSnapshot(),
  };
}

/** True only while the exact runtime and generation that started the work remain active. */
export function isRuntimeContextCurrent(context: ReaderRuntimeContext | null | undefined): boolean {
  return Boolean(
    context &&
      getActiveRuntimeSnapshot() === context.runtime &&
      getConnectionGenerationSnapshot() === context.generation &&
      getReaderSessionEpochSnapshot() === context.sessionEpoch &&
      context.runtime.generation === context.generation &&
      context.runtime.identity.server_id === context.serverId,
  );
}

/** Run a callback only when its captured runtime context is still current. */
export function withCurrentRuntimeContext<T>(
  context: ReaderRuntimeContext | null | undefined,
  callback: (context: ReaderRuntimeContext) => T,
): T | undefined {
  if (!isRuntimeContextCurrent(context)) return undefined;
  return callback(context as ReaderRuntimeContext);
}

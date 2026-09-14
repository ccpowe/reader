// Public runtime boundary used by API callers and tests.  Keeping this alias
// avoids reintroducing static server configuration at individual call sites.
export {
  activateReaderServer,
  connectReaderServer,
  createReaderRuntime,
  disconnectReaderServer,
  getActiveRuntime,
  getConnectionGeneration,
  readerApiUrl,
  subscribeConnection,
  type ConnectionListener,
  type RuntimeActivationOptions,
} from './connection/runtime';
export {
  captureRuntimeContext,
  isRuntimeContextCurrent,
  withCurrentRuntimeContext,
  type ReaderRuntimeContext,
} from './connection/guard';
export { logoutReaderSession, type LogoutReaderSessionOptions } from './connection/session';
export { resolveReaderUrl as resolveUrl } from './connection/url';
export type { ActiveReaderRuntime } from './connection/types';

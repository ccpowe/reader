// Compatibility entry point for feature code that treats discovery as a
// top-level mobile concern.  The implementation lives with the connection
// state machine so it cannot be used without the same URL/error contract.
export {
  discoverReaderServer,
  redirectConfirmationFor,
  type DiscoverOptions,
} from './connection/discovery';
export type { ReaderDiscovery, ReaderDiscoveryResult } from './connection/types';

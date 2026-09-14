import type { ReaderAuthClient } from '../readerAuth';

export type ReaderBaseUrl = string & { readonly __readerBaseUrl: unique symbol };

export type ReaderDiscovery = {
  protocol_version: 2;
  server_id: string;
  api_base_url: string;
};

export type ReaderDiscoveryResult = ReaderDiscovery & {
  /** The normalized URL the user entered. */
  base_url: ReaderBaseUrl;
  /** Exact discovery URL requested from ``base_url``. */
  discovery_url: string;
  /** Final URL reported by platform fetch after redirects. */
  response_url: string;
  /** True when fetch followed a redirect to another origin. */
  redirected: boolean;
  /** Activation must pause for an explicit user confirmation. */
  requires_confirmation: boolean;
};

export type ReaderConnectionIdentity = {
  server_id: string;
  api_base_url: string;
};

export type PersistedConnection = {
  base_url: ReaderBaseUrl;
  identity: ReaderConnectionIdentity;
  /**
   * Public coordinates from the last trusted discovery response.  Legacy
   * entries without this cache are upgraded on the next successful connect.
   */
  discovery?: ReaderDiscovery;
};

export type ConnectionPhase =
  | 'idle'
  | 'restoring'
  | 'discovering'
  | 'awaiting_redirect_confirmation'
  | 'activating'
  | 'active'
  | 'error';

export type ConnectionState = {
  phase: ConnectionPhase;
  generation: number;
  active: ActiveReaderRuntime | null;
  candidate: ReaderDiscoveryResult | null;
  error: import('./errors').ConnectionError | null;
};

export type ActiveReaderRuntime = {
  generation: number;
  base_url: ReaderBaseUrl;
  api_base_url: string;
  identity: ReaderConnectionIdentity;
  discovery: ReaderDiscovery;
  authClient: ReaderAuthClient;
  serverToken: string;
  /** Optional lifecycle hooks used by test doubles and host integrations. */
  lifecycle?: {
    startAutoRefresh?: () => void | Promise<void>;
    stopAutoRefresh?: () => void | Promise<void>;
    unsubscribe?: () => void;
  };
};

export type RedirectConfirmation = {
  from_url: string;
  to_url: string;
  from_origin: string;
  to_origin: string;
};

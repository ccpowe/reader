import { useCallback, useEffect, useRef, useState } from 'react';
// Install Expo's persistent localStorage adapter before connection metadata is
// read during cold start. Credentials use platform-specific private storage.
import 'expo-sqlite/localStorage/install';
import {
  ActivityIndicator,
  Platform,
  StyleSheet,
  Text,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import type { Session } from './lib/readerAuth';
import { QueryClientProvider } from '@tanstack/react-query';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { queryClient } from './state/queryClient';
import {
  activateReaderServer,
  discoverReaderServer,
  getConnectionGeneration,
  getActiveRuntime,
  identityConfirmationFor,
  logoutReaderSession,
  readerIdentitiesMatch,
  readPersistedConnection,
  readLegacyServerAddress,
  subscribeServerTokenInvalid,
  notifyServerTokenInvalid,
  restorePersistedReaderRuntime,
  subscribeConnection,
  updateReaderSessionIdentity,
  writePersistedConnection,
  ConnectionError,
  type ReaderDiscoveryResult,
  type ActiveReaderRuntime,
  type ReaderConnectionIdentity,
  type ReaderIdentityConfirmation,
} from './lib/connection';
import { ActionConfirm } from './components/ActionConfirm';
import { EmailAuthScreen } from './screens/EmailAuthScreen';
import { HomeUiDraftPreview } from './screens/HomeUiDraftPreview';
import { MainAppShell } from './components/MainAppShell';
import { RedirectConfirm } from './components/RedirectConfirm';
import { ServerConnectionForm } from './components/ServerConnectionForm';
import { colors } from './ui/tokens';
import { clearTranslationDiagnostics, recordTranslationDiagnostic } from './domain/translationDiagnostics';
import appConfig from './app.json';
import { AppLanguageProvider } from './i18n/AppLanguageProvider';
import { useTranslation } from './i18n';

type AppAuthState = {
  runtime: ActiveReaderRuntime | null;
  session: Session | null;
  loading: boolean;
};

type PendingIdentityConfirmation = {
  discovery: ReaderDiscoveryResult;
  expectedIdentity: ReaderConnectionIdentity;
  details: ReaderIdentityConfirmation;
  signal?: AbortSignal;
};

export default function App() {
  if (
    __DEV__ &&
    Platform.OS === 'web' &&
    typeof window !== 'undefined' &&
    new URLSearchParams(window.location.search).get('ui-preview') === 'home'
  ) {
    return (
      <SafeAreaProvider>
        <HomeUiDraftPreview />
      </SafeAreaProvider>
    );
  }

  return <AppLanguageProvider><ReaderApp /></AppLanguageProvider>;
}

function ReaderApp() {
  const { t } = useTranslation('app');
  const [{ runtime, session, loading }, setAuthState] = useState<AppAuthState>(() => ({
    loading: true,
    runtime: getActiveRuntime(),
    session: null,
  }));
  const [connectionPhase, setConnectionPhase] = useState<'idle' | 'restoring' | 'discovering' | 'awaiting_redirect_confirmation' | 'awaiting_identity_confirmation' | 'activating' | 'error' | 'active'>('restoring');
  const [connectionError, setConnectionError] = useState<ConnectionError | null>(null);
  const [candidate, setCandidate] = useState<ReaderDiscoveryResult | null>(null);
  const [identityConfirmation, setIdentityConfirmation] = useState<PendingIdentityConfirmation | null>(null);
  const [lastSubmittedBase, setLastSubmittedBase] = useState<string | null>(null);
  const [showConnectionForm, setShowConnectionForm] = useState(false);
  const submittedTokenRef = useRef<string | undefined>(undefined);
  const discoveryAbortRef = useRef<AbortController | null>(null);
  const authCleanupRef = useRef<(() => void) | null>(null);
  const bootstrappedRef = useRef(false);
  const previousGenerationRef = useRef(0);
  const expectedIdentityRef = useRef<ReaderConnectionIdentity | null>(null);
  const candidateRef = useRef<ReaderDiscoveryResult | null>(null);
  const identityConfirmationRef = useRef<PendingIdentityConfirmation | null>(null);

  useEffect(() => {
    clearTranslationDiagnostics();
    recordTranslationDiagnostic('session_boundary', {
      stage: 'lifecycle', appVersion: appConfig.expo.version,
      status: session?.user.id ? 'authenticated' : 'signed_out',
    });
  }, [runtime?.generation, session?.user.id]);

  useEffect(() => subscribeConnection((nextRuntime) => {
    // Publishing a runtime is one state transition: never render the old
    // session while the new server's namespace is being restored.
    setAuthState({
      loading: Boolean(nextRuntime),
      runtime: nextRuntime,
      session: null,
    });
    setConnectionPhase(nextRuntime ? 'active' : 'idle');
  }), []);

  // A runtime change is the cache and listener boundary.  Queries from the
  // previous server are cancelled before their cache is discarded; keys also
  // include server_id so a delayed result cannot populate the new namespace.
  useEffect(() => {
    const nextGeneration = runtime?.generation ?? 0;
    if (previousGenerationRef.current === nextGeneration) return;
    previousGenerationRef.current = nextGeneration;
    void queryClient.cancelQueries().catch(() => undefined);
    queryClient.clear();
  }, [runtime]);

  useEffect(() => {
    if (!runtime) {
      setAuthState((current) => ({ ...current, loading: false, session: null }));
      return;
    }
    let mounted = true;
    const runtimeGeneration = runtime.generation;
    let authEventVersion = 0;
    const publishSession = (nextSession: Session | null) => {
      if (updateReaderSessionIdentity(runtime, nextSession?.user.id ?? null)) {
        // Clear synchronously: a late cancellation must not clear B's cache.
        void queryClient.cancelQueries().catch(() => undefined);
        queryClient.clear();
      }
      setAuthState((current) => ({ ...current, loading: false, session: nextSession }));
    };
    setAuthState((current) => ({ ...current, loading: true }));
    const restore = async () => {
      const version = authEventVersion;
      const result = await runtime.authClient.auth.getSession();
      if (mounted && version === authEventVersion && getConnectionGeneration() === runtimeGeneration && getActiveRuntime() === runtime) {
        publishSession(result.data.session);
      }
    };
    void restore().catch(() => {
      if (mounted && authEventVersion === 0 && getConnectionGeneration() === runtimeGeneration && getActiveRuntime() === runtime) {
        publishSession(null);
      }
    });
    const { data: listener } = runtime.authClient.auth.onAuthStateChange((_event, nextSession) => {
      if (!mounted || getConnectionGeneration() !== runtimeGeneration) return;
      if (getActiveRuntime() !== runtime) return;
      authEventVersion += 1;
      publishSession(nextSession);
    });
    const cleanup = () => {
      mounted = false;
      listener.subscription.unsubscribe();
      if (authCleanupRef.current === cleanup) authCleanupRef.current = null;
    };
    authCleanupRef.current = cleanup;
    return () => {
      cleanup();
    };
  }, [runtime]);

  useEffect(() => {
    if (bootstrappedRef.current) return;
    bootstrappedRef.current = true;
    const persisted = readPersistedConnection();
    if (!persisted) {
      setLastSubmittedBase(readLegacyServerAddress());
      expectedIdentityRef.current = null;
      setAuthState((current) => ({ ...current, loading: false }));
      setConnectionPhase('idle');
      setShowConnectionForm(true);
      return;
    }
    setLastSubmittedBase(persisted.base_url);
    expectedIdentityRef.current = persisted.identity;
    const controller = new AbortController();
    discoveryAbortRef.current = controller;
    const revalidateInBackground = async () => {
      try {
        const nextCandidate = await discoverReaderServer(persisted.base_url, { signal: controller.signal });
        if (controller.signal.aborted) return;
        const nextIdentity = {
          server_id: nextCandidate.server_id,
          api_base_url: nextCandidate.api_base_url,
        };
        if (
          !nextCandidate.requires_confirmation &&
          readerIdentitiesMatch(persisted.identity, nextIdentity)
        ) {
          // The server remains the same trusted deployment.  Keep the active
          // runtime uninterrupted, but use the newest public coordinates next
          // time the app starts.
          writePersistedConnection({
            base_url: nextCandidate.base_url,
            discovery: nextCandidate,
            identity: nextIdentity,
          });
          return;
        }
        await activateCandidate(nextCandidate, { signal: controller.signal });
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof ConnectionError && error.code === 'server_token_invalid') {
          const failedRuntime = getActiveRuntime();
          if (failedRuntime?.base_url === persisted.base_url) notifyServerTokenInvalid(failedRuntime);
          setConnectionError(error);
          setConnectionPhase('error');
          setShowConnectionForm(true);
        }
        // An offline or temporarily unavailable server must not replace a
        // cached, already authenticated runtime with the connection form.
        if (controller.signal.aborted) return;
      }
    };
    if (persisted.discovery) {
      setConnectionPhase('restoring');
      void restorePersistedReaderRuntime(persisted, { signal: controller.signal })
        .then(({ runtime: restoredRuntime, session: restoredSession }) => {
          if (controller.signal.aborted || getActiveRuntime() !== restoredRuntime) return;
          // The session read that committed this runtime has already completed,
          // so do not render a second foreground "restoring" transition before
          // showing the app.
          setAuthState({ loading: false, runtime: restoredRuntime, session: restoredSession });
          setConnectionPhase('active');
          void revalidateInBackground();
        })
        .catch(() => {
          if (controller.signal.aborted) return;
          void discoverReaderServer(persisted.base_url, { signal: controller.signal })
            .then((nextCandidate) => activateCandidate(nextCandidate, { signal: controller.signal }))
            .catch((error) => {
              if (controller.signal.aborted) return;
              setConnectionError(error instanceof Error ? error as ConnectionError : null);
              setConnectionPhase('error');
              setShowConnectionForm(true);
            });
        });
      return () => controller.abort();
    }
    setConnectionPhase('restoring');
    void discoverReaderServer(persisted.base_url, { signal: controller.signal })
      .then((nextCandidate) => activateCandidate(nextCandidate, { signal: controller.signal }))
      .catch((error) => {
        if (controller.signal.aborted) return;
        setConnectionError(error instanceof Error ? error as ConnectionError : null);
        setConnectionPhase('error');
        setShowConnectionForm(true);
      });
    return () => controller.abort();
    // This bootstrap is intentionally single-shot. activateCandidate is read
    // only after the component has finished rendering and its implementation
    // operates through generation-guarded connection state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => subscribeServerTokenInvalid((failedRuntime) => {
    if (getActiveRuntime() !== failedRuntime) return;
    setConnectionError(new ConnectionError('server_token_invalid', t('serverTokenInvalid')));
    setConnectionPhase('error');
    setShowConnectionForm(true);
  }), [t]);

  useEffect(() => () => {
    discoveryAbortRef.current?.abort();
    authCleanupRef.current?.();
  }, []);

  const logoutCurrentSession = useCallback(async () => {
    const currentRuntime = getActiveRuntime();
    if (!currentRuntime) return;
    try {
      await logoutReaderSession({
        cancelQueries: () => queryClient.cancelQueries(),
        clearCaches: () => {
          queryClient.getQueryCache().clear();
          queryClient.getMutationCache().clear();
        },
        clearSession: () => {
          setAuthState((current) => ({ ...current, loading: false, session: null }));
        },
        runtime: currentRuntime,
        stopAuthListener: () => {
          authCleanupRef.current?.();
          authCleanupRef.current = null;
        },
      });
    } catch {
      // Local session/cache cleanup and the auth namespace transition happen
      // in logoutReaderSession.finally even when the remote sign-out fails.
    }
  }, []);

  const activateCandidate = useCallback(async (
    nextCandidate: ReaderDiscoveryResult,
    {
      confirmedIdentity = false,
      confirmedRedirect = false,
      signal,
      throwErrors = false,
    }: {
      confirmedIdentity?: boolean;
      confirmedRedirect?: boolean;
      signal?: AbortSignal;
      throwErrors?: boolean;
    } = {},
  ) => {
    setConnectionError(null);
    candidateRef.current = nextCandidate;
    setCandidate(nextCandidate);
    if (nextCandidate.requires_confirmation && !confirmedRedirect) {
      identityConfirmationRef.current = null;
      setIdentityConfirmation(null);
      setConnectionPhase('awaiting_redirect_confirmation');
      setShowConnectionForm(true);
      return;
    }
    const expectedIdentity = expectedIdentityRef.current;
    const discoveredIdentity = {
      server_id: nextCandidate.server_id,
      api_base_url: nextCandidate.api_base_url,
    };
    if (
      expectedIdentity &&
      !readerIdentitiesMatch(expectedIdentity, discoveredIdentity) &&
      !confirmedIdentity
    ) {
      const pending: PendingIdentityConfirmation = {
        details: identityConfirmationFor(expectedIdentity, discoveredIdentity),
        discovery: nextCandidate,
        expectedIdentity,
        signal,
      };
      identityConfirmationRef.current = pending;
      setIdentityConfirmation(pending);
      setConnectionPhase('awaiting_identity_confirmation');
      setShowConnectionForm(true);
      return;
    }
    identityConfirmationRef.current = null;
    setIdentityConfirmation(null);
    if (candidateRef.current !== nextCandidate || signal?.aborted) return;
    setConnectionPhase('activating');
    try {
      await activateReaderServer(nextCandidate, {
        confirmedIdentity,
        confirmedRedirect,
        expectedIdentity: expectedIdentity ?? undefined,
        serverToken: submittedTokenRef.current,
        signal,
      });
      if (candidateRef.current !== nextCandidate || signal?.aborted) return;
      expectedIdentityRef.current = null;
      identityConfirmationRef.current = null;
      setIdentityConfirmation(null);
      candidateRef.current = null;
      setCandidate(null);
      setShowConnectionForm(false);
      setConnectionPhase('active');
    } catch (error) {
      if (
        signal?.aborted ||
        candidateRef.current !== nextCandidate ||
        (error instanceof ConnectionError && error.code === 'stale_runtime')
      ) return;
      if (throwErrors) {
        setConnectionPhase('awaiting_identity_confirmation');
        throw error;
      }
      setConnectionError(error instanceof Error ? error as ConnectionError : null);
      setConnectionPhase('error');
      setShowConnectionForm(true);
    }
  }, []);

  const confirmIdentity = useCallback(async () => {
    const pending = identityConfirmationRef.current;
    if (!pending) return;
    await activateCandidate(pending.discovery, {
      confirmedIdentity: true,
      confirmedRedirect: pending.discovery.requires_confirmation,
      signal: pending.signal,
      throwErrors: true,
    });
  }, [activateCandidate]);

  const isIdentityConfirmationCurrent = useCallback(() => {
    const pending = identityConfirmationRef.current;
    return Boolean(
      pending &&
      identityConfirmation === pending &&
      candidateRef.current === pending.discovery &&
      !pending.signal?.aborted,
    );
  }, [identityConfirmation]);

  const connectFromForm = useCallback(async (value: string, serverToken: string) => {
    discoveryAbortRef.current?.abort();
    const controller = new AbortController();
    discoveryAbortRef.current = controller;
    submittedTokenRef.current = serverToken.trim();
    setLastSubmittedBase(value.trim() || null);
    setConnectionError(null);
    candidateRef.current = null;
    identityConfirmationRef.current = null;
    setIdentityConfirmation(null);
    setCandidate(null);
    setConnectionPhase('discovering');
    try {
      const nextCandidate = await discoverReaderServer(value, { signal: controller.signal, serverToken: serverToken.trim() });
      await activateCandidate(nextCandidate, { signal: controller.signal });
    } catch (error) {
      if (controller.signal.aborted) return;
      setConnectionError(error instanceof Error ? error as ConnectionError : null);
      setConnectionPhase('error');
    }
  }, [activateCandidate]);

  const cancelConnection = useCallback(() => {
    discoveryAbortRef.current?.abort();
    candidateRef.current = null;
    identityConfirmationRef.current = null;
    setIdentityConfirmation(null);
    setCandidate(null);
    setConnectionError(null);
    setShowConnectionForm(false);
    setConnectionPhase(runtime ? 'active' : 'idle');
  }, [runtime]);

  const connectionOverlay = showConnectionForm || !runtime ? (
    <SafeAreaView edges={runtime ? ['top', 'bottom'] : []} style={[runtime ? styles.connectionOverlay : styles.connectionPage, (connectionPhase === 'awaiting_identity_confirmation' || connectionPhase === 'awaiting_redirect_confirmation') && styles.connectionConfirmation]}>
      {connectionPhase === 'awaiting_identity_confirmation' && identityConfirmation ? (
        <ActionConfirm
          cancelLabel={t('cancelConnection')}
          confirmLabel={t('confirmConnection')}
          isCurrent={isIdentityConfirmationCurrent}
          message={t('identityMessage', { previous: identityConfirmation.details.previous_server_id, next: identityConfirmation.details.next_server_id })}
          note={t('identityNote', { previous: identityConfirmation.details.previous_api_base_url, next: identityConfirmation.details.next_api_base_url })}
          onCancel={cancelConnection}
          onConfirm={confirmIdentity}
          title={t('confirmIdentity')}
        />
      ) : connectionPhase === 'awaiting_redirect_confirmation' && candidate ? (
        <RedirectConfirm
          discovery={candidate}
          onCancel={cancelConnection}
          onConfirm={() => void activateCandidate(candidate, {
            confirmedRedirect: true,
            signal: discoveryAbortRef.current?.signal,
          })}
        />
      ) : (
        <ServerConnectionForm busy={connectionPhase === 'discovering' || connectionPhase === 'restoring' || connectionPhase === 'activating'} error={connectionError} initialValue={runtime?.base_url ?? lastSubmittedBase ?? undefined} onSubmit={connectFromForm} onCancel={runtime ? cancelConnection : undefined} />
      )}
    </SafeAreaView>
  ) : null;

  return (
    <QueryClientProvider client={queryClient}>
      <SafeAreaProvider>
        <GestureHandlerRootView style={styles.app}>
          <SafeAreaView edges={['top', 'bottom']} style={styles.app}>
            {loading && runtime ? <CenteredMessage loading message={t('restoringSession')} /> : null}
            {!loading && runtime && !session ? <EmailAuthScreen key={`${runtime.identity.server_id}:${runtime.generation}`} serverUrl={runtime.base_url} client={runtime.authClient} generation={runtime.generation} onChangeServer={() => setShowConnectionForm(true)} /> : null}
            {!loading && runtime && session ? <MainAppShell key={`${runtime.identity.server_id}:${runtime.generation}:${session.user.id}`} onChangeServer={() => setShowConnectionForm(true)} onLogout={logoutCurrentSession} session={session} authClient={runtime.authClient} /> : null}
            {connectionOverlay}
            <StatusBar style="dark" />
          </SafeAreaView>
        </GestureHandlerRootView>
      </SafeAreaProvider>
    </QueryClientProvider>
  );
}

function CenteredMessage({ loading = false, message }: { loading?: boolean; message: string }) { return <SafeAreaView style={styles.centered}>{loading ? <ActivityIndicator color={colors.accent} /> : null}<Text style={styles.emptyText}>{message}</Text></SafeAreaView>; }

const styles = StyleSheet.create({
  app: { backgroundColor: colors.background, flex: 1 },
  connectionPage: { alignItems: 'stretch', backgroundColor: colors.background, flex: 1, justifyContent: 'flex-start' },
  connectionOverlay: { alignItems: 'stretch', backgroundColor: colors.background, bottom: 0, justifyContent: 'flex-start', left: 0, position: 'absolute', right: 0, top: 0, zIndex: 50 },
  connectionConfirmation: { alignItems: 'center', justifyContent: 'center', padding: 24 },
  centered: { alignItems: 'center', backgroundColor: colors.background, flex: 1, gap: 16, justifyContent: 'center', padding: 24 },
  emptyText: { color: colors.textTertiary, fontSize: 15, lineHeight: 23, textAlign: 'center' },
});

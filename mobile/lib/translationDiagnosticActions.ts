import { i18n } from '../i18n';
import { Alert, Share } from 'react-native';
import {
  TRANSLATION_DIAGNOSTICS_ENABLED,
  exportTranslationDiagnostics,
  setTranslationDiagnosticsDetailed,
} from '../domain/translationDiagnostics';

/** User-triggered only: diagnostics stay local until the user shares the export. */
function openLogOptions(snapshot?: () => string) {
  Alert.alert(i18n.t('errors:diagnosticsTitle'), i18n.t('errors:diagnosticsPrivacy'), [
    { text: i18n.t('errors:cancel'), style: 'cancel' },
    { text: i18n.t('errors:enableDetailedLogs'), onPress: () => {
      setTranslationDiagnosticsDetailed(true);
      Alert.alert(i18n.t('errors:detailedLogsEnabled'), i18n.t('errors:reproduceForLogs'));
    } },
    { text: i18n.t('errors:exportLogs'), onPress: () => {
      void Share.share({ message: snapshot?.() ?? exportTranslationDiagnostics(), title: i18n.t('errors:diagnosticsTitle') })
        .catch(() => Alert.alert(i18n.t('errors:exportDiagnosticsFailed'), i18n.t('errors:tryAgainLater')));
    } },
  ]);
}

export function openTranslationDiagnostics(snapshot?: () => string, inspectSelection?: (onResult: (message: string) => void) => void) {
  if (!TRANSLATION_DIAGNOSTICS_ENABLED) return;
  if (!inspectSelection) { openLogOptions(snapshot); return; }
  Alert.alert(i18n.t('errors:diagnosticsTitle'), i18n.t('errors:diagnosticsSelectionHint'), [
    { text: i18n.t('errors:cancel'), style: 'cancel' },
    { text: i18n.t('errors:inspectSelectedPassage'), onPress: () => inspectSelection((message) => Alert.alert(i18n.t('errors:selectionDiagnostics'), message)) },
    { text: i18n.t('errors:logOptions'), onPress: () => openLogOptions(snapshot) },
  ]);
}

import { useLayoutEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { Feather } from '@expo/vector-icons';
import type { Session } from '../lib/readerAuth';
import { useQueryClient } from '@tanstack/react-query';

import { sourcePlaceholder } from '../domain/source';
import { UNCATEGORIZED_FOLDER } from '../domain/folders';
import {
  addRedditSource,
  addRssSource,
  addWebSource,
  addXSource,
  addYouTubeSource,
} from '../lib/api';
import { captureRuntimeContext, isRuntimeContextCurrent } from '../lib/connection';
import { useReaderRuntime } from '../lib/connection/react';
import { invalidateAfterSourceMutation } from '../state/invalidation';
import { BottomSheetModal } from './BottomSheetModal';
import { SourceFolderChoices } from './SourceFolderChoices';
import { useTranslation } from '../i18n';

type SourceKind = 'rss' | 'web' | 'reddit' | 'youtube' | 'x';

export function AddSourceSheet({
  folders,
  initialFolder,
  onAdded,
  onClose,
  session,
  visible,
}: {
  folders: string[];
  initialFolder: string;
  onAdded?: () => void;
  onClose: () => void;
  session: Session;
  visible: boolean;
}) {
  const { t } = useTranslation('feed');
  const [submitting, setSubmitting] = useState(false);
  const [url, setUrl] = useState('');
  const [kind, setKind] = useState<SourceKind>('rss');
  const [folder, setFolder] = useState(initialFolder);
  const [newFolder, setNewFolder] = useState('');
  const [errorMessage, setErrorMessage] = useState<Error | true | null>(null);
  const readerClient = useQueryClient();
  const runtime = useReaderRuntime();
  const mountedRef = useRef(true);
  const submittingRef = useRef(false);

  useLayoutEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  async function add() {
    if (submittingRef.current || !url.trim()) return;
    const context = captureRuntimeContext(runtime);
    if (!context) return;
    submittingRef.current = true;
    setSubmitting(true);
    setErrorMessage(null);
    try {
      const folderName = newFolder.trim() || (folder === UNCATEGORIZED_FOLDER ? null : folder);
      if (kind === 'rss') await addRssSource(session, url.trim(), folderName, context.runtime);
      else if (kind === 'web') await addWebSource(session, url.trim(), folderName, context.runtime);
      else if (kind === 'reddit') await addRedditSource(session, url.trim(), folderName, context.runtime);
      else if (kind === 'youtube') await addYouTubeSource(session, url.trim(), folderName, context.runtime);
      else await addXSource(session, url.trim(), folderName, context.runtime);
      if (!mountedRef.current || !isRuntimeContextCurrent(context)) return;
      onAdded?.();
      onClose();
      void invalidateAfterSourceMutation(readerClient, session.user.id, context.serverId, context).catch(() => undefined);
    } catch (error) {
      if (!mountedRef.current || !isRuntimeContextCurrent(context)) return;
      setErrorMessage(error instanceof Error ? error : true);
    } finally {
      if (mountedRef.current && isRuntimeContextCurrent(context)) {
        submittingRef.current = false;
        setSubmitting(false);
      }
    }
  }

  function selectKind(nextKind: SourceKind) {
    setErrorMessage(null);
    setKind(nextKind);
  }

  return (
    <BottomSheetModal constrainHeight={false} contentStyle={styles.sheet} onClose={onClose} visible={visible}>
      <View style={styles.modalHandle} />
      <View style={styles.heading}>
        <Text accessibilityRole="header" style={styles.modalTitle}>{t('addSource')}</Text>
        <Pressable accessibilityLabel={t('close')} accessibilityRole="button" onPress={onClose} style={styles.closeButton}>
          <Feather name="x" size={20} color="#777B74" />
        </Pressable>
      </View>
      <View style={styles.section}>
        <Text style={styles.fieldLabel}>{t('sourceType')}</Text>
        <View style={styles.kindSwitch}>
          <KindButton active={kind === 'rss'} disabled={submitting} label="RSS" onPress={() => selectKind('rss')} />
          <KindButton active={kind === 'web'} disabled={submitting} label={t('webSource')} onPress={() => selectKind('web')} />
          <KindButton active={kind === 'reddit'} disabled={submitting} label="Reddit" onPress={() => selectKind('reddit')} />
          <KindButton active={kind === 'youtube'} disabled={submitting} label="YouTube" onPress={() => selectKind('youtube')} />
          <KindButton active={kind === 'x'} disabled={submitting} label="X" onPress={() => selectKind('x')} />
        </View>
      </View>
      <View style={styles.section}>
        <Text style={styles.fieldLabel}>{t('sourceAddress')}</Text>
        <View style={styles.inputFrame}>
          <Feather color="#92958C" name="link" size={17} />
          <TextInput
            accessibilityLabel={sourcePlaceholder(kind)}
            autoCapitalize="none"
            autoCorrect={false}
            editable={!submitting}
            keyboardType={kind === 'rss' || kind === 'web' ? 'url' : 'default'}
            onChangeText={(value) => { setErrorMessage(null); setUrl(value); }}
            placeholder={sourcePlaceholder(kind)}
            placeholderTextColor="#92958C"
            style={styles.input}
            value={url}
          />
        </View>
        {kind === 'rss' || kind === 'web' || kind === 'reddit' ? (
          <Text style={styles.modalHint}>
            {t(kind === 'web' ? 'webSourceHint' : kind === 'reddit' ? 'redditSourceHint' : 'sourceAddHint')}
          </Text>
        ) : null}
      </View>
      <View style={styles.section}>
        <Text style={styles.fieldLabel}>{t('folder')}</Text>
        <SourceFolderChoices
          folders={folders}
          selected={folder}
          newFolder={newFolder}
          onSelect={(nextFolder) => { if (!submitting) { setFolder(nextFolder); setNewFolder(''); } }}
        />
        <View style={styles.inputFrame}>
          <Feather color="#92958C" name="plus" size={17} />
          <TextInput
            accessibilityLabel={t('createFolder')}
            autoCapitalize="sentences"
            editable={!submitting}
            onChangeText={setNewFolder}
            placeholder={t('createFolderPlaceholder')}
            placeholderTextColor="#92958C"
            style={styles.input}
            value={newFolder}
          />
        </View>
      </View>
      {errorMessage ? <Text accessibilityLiveRegion="assertive" style={styles.formError}>{errorMessage === true ? t('sourceAddFailed') : errorMessage.message}</Text> : null}
      <Pressable accessibilityRole="button" accessibilityState={{ disabled: submitting || !url.trim(), busy: submitting }} disabled={submitting || !url.trim()} onPress={() => void add()} style={({ pressed }) => [styles.primaryButton, !url.trim() && styles.primaryButtonDisabled, pressed && styles.pressed]}>
        {submitting ? <ActivityIndicator color="#FFFFFF" size="small" /> : null}
        <Text style={styles.primaryButtonText}>{t(submitting ? 'addingSource' : 'addAndSync')}</Text>
      </Pressable>
    </BottomSheetModal>
  );
}

function KindButton({ active, disabled, label, onPress }: { active: boolean; disabled: boolean; label: string; onPress: () => void }) {
  return (
    <Pressable accessibilityLabel={label} accessibilityRole="tab" accessibilityState={{ disabled, selected: active }} disabled={disabled} onPress={onPress} style={({ pressed }) => [styles.kindButton, active && styles.kindButtonActive, pressed && styles.pressed]}>
      <Text style={[styles.kindText, active && styles.kindTextActive]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  // Let the outer ScrollView measure the whole form when the keyboard reduces
  // the viewport; a capped inner sheet leaves the submit button off its surface.
  sheet: { backgroundColor: '#FCFBF8', borderTopLeftRadius: 26, borderTopRightRadius: 26 },
  modalHandle: { alignSelf: 'center', backgroundColor: '#D9DAD2', borderRadius: 2, height: 4, marginBottom: 16, width: 32 },
  heading: { alignItems: 'center', flexDirection: 'row', gap: 12, marginBottom: 24 },
  modalTitle: { color: '#242924', flex: 1, fontSize: 24, fontWeight: '700', lineHeight: 32 },
  closeButton: { alignItems: 'center', backgroundColor: '#F0F0EA', borderRadius: 22, height: 44, justifyContent: 'center', width: 44 },
  section: { marginBottom: 24 },
  modalHint: { color: '#777B74', fontSize: 12, lineHeight: 19, marginTop: 10 },
  kindSwitch: { backgroundColor: '#F0F0EA', borderRadius: 12, flexDirection: 'row', flexWrap: 'wrap', gap: 3, padding: 4 },
  kindButton: { alignItems: 'center', borderRadius: 9, flexBasis: 48, flexGrow: 1, justifyContent: 'center', minHeight: 44, paddingHorizontal: 4, paddingVertical: 10 },
  kindButtonActive: { backgroundColor: '#242424' },
  kindText: { color: '#777B74', fontSize: 12, fontWeight: '600' },
  kindTextActive: { color: '#FFFFFF' },
  fieldLabel: { color: '#555B51', fontSize: 13, fontWeight: '600', lineHeight: 19, marginBottom: 10 },
  inputFrame: { alignItems: 'center', backgroundColor: '#FFFFFF', borderColor: '#E8E9E2', borderRadius: 12, borderWidth: 1, flexDirection: 'row', gap: 10, paddingHorizontal: 14 },
  input: { color: '#242924', flex: 1, fontSize: 14, minHeight: 50, minWidth: 0, paddingVertical: 13 },
  formError: { color: '#BA1A1A', fontSize: 13, lineHeight: 19, marginBottom: 12 },
  primaryButton: { alignItems: 'center', backgroundColor: '#242424', borderRadius: 12, flexDirection: 'row', gap: 10, justifyContent: 'center', minHeight: 52, paddingHorizontal: 16, paddingVertical: 12 },
  primaryButtonDisabled: { opacity: 0.38 },
  primaryButtonText: { color: '#FFFFFF', fontSize: 15, fontWeight: '600' },
  pressed: { opacity: 0.7 },
});

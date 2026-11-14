/**
 * 个人资料 — assembly: the display name (saved on 保存; the input stays on failure) and the avatar
 * (pick a square image from the library, or remove it).
 */
import { useQuery } from '@tanstack/react-query';
import * as ImagePicker from 'expo-image-picker';
import { router, useFocusEffect } from 'expo-router';
import { useCallback, useRef, useState } from 'react';

import { useIdentity, useSession } from '@/lib/app-session';
import { absoluteUrl } from '@/lib/feed';
import { displayNameInput } from '@/lib/me';
import { AccountChanged, removeAvatar, updateDisplayName, uploadAvatar } from '@/lib/queries/me';
import { meQuery } from '@/lib/queries/today';
import { errorMessage } from '@/lib/user-error';
import { ChoiceList, ProfileView } from '@/screens/MeView';
import { PushedPage } from '@/screens/PushedPage';
import { EmptyState, Message, Sheet, Spinner } from '@/ui';
import { t } from '@/lib/i18n';
import { useLanguage } from '@/lib/interface-language';

const AVATAR_TYPES = ['image/jpeg', 'image/png', 'image/webp'];

export default function Profile() {
  useLanguage(); // texts follow the interface language
  const identity = useIdentity();
  const current = useSession();
  const me = useQuery(meQuery(identity));
  const [saving, setSaving] = useState(false);
  const [nameError, setNameError] = useState<string | null>(null);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const [avatarError, setAvatarError] = useState<string | null>(null);
  const [avatarSheet, setAvatarSheet] = useState(false);
  // A save that finishes after the user left this page does not navigate (react-guidelines §1).
  const visible = useRef(true);
  useFocusEffect(
    useCallback(() => {
      visible.current = true;
      return () => {
        visible.current = false;
      };
    }, []),
  );
  const back = () => (router.canGoBack() ? router.back() : router.replace('/me'));
  const apiBase = current.status === 'signed-in' ? current.server.apiBaseUrl : '';

  async function save(typed: string) {
    if (saving) return;
    setSaving(true);
    setNameError(null);
    try {
      await updateDisplayName(identity, displayNameInput(typed));
      if (visible.current) back();
    } catch (error) {
      if (error instanceof AccountChanged) return;
      setNameError(t().me.notSaved(errorMessage(error)));
    } finally {
      setSaving(false);
    }
  }

  async function avatar(task: () => Promise<void>) {
    setAvatarBusy(true);
    setAvatarError(null);
    try {
      await task();
    } catch (error) {
      if (error instanceof AccountChanged) return;
      setAvatarError(t().me.avatarFailed(errorMessage(error)));
    } finally {
      setAvatarBusy(false);
    }
  }

  function pick() {
    setAvatarSheet(false);
    // Picking, checking and uploading are one operation: any failure shows as an avatar failure.
    return avatar(async () => {
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ['images'],
        allowsEditing: true,
        aspect: [1, 1],
        quality: 0.85,
      });
      const asset = result.canceled ? null : result.assets[0];
      if (!asset) return;
      const type = asset.mimeType ?? 'image/jpeg';
      if (!AVATAR_TYPES.includes(type)) return setAvatarError(t().me.avatarType);
      await uploadAvatar(identity, asset.uri, type);
    });
  }

  let body;
  if (me.data) {
    body = (
      <>
        {avatarError ? (
          <Message bar kind="danger">
            {avatarError}
          </Message>
        ) : null}
        <ProfileView
          avatarBusy={avatarBusy}
          avatarUrl={absoluteUrl(apiBase)(me.data.avatar_url)}
          email={me.data.email}
          error={nameError}
          key={me.data.id}
          name={me.data.display_name ?? ''}
          onAvatar={() => setAvatarSheet(true)}
          onEdit={() => setNameError(null)}
          onSave={(typed) => void save(typed)}
          saving={saving}
        />
      </>
    );
  } else if (me.isError) {
    body = <EmptyState description={errorMessage(me.error)} title={t().common.loadFailed} />;
  } else {
    body = <Spinner />;
  }

  return (
    <PushedPage onBack={back} title={t().me.profile}>
      {body}
      <Sheet onClose={() => setAvatarSheet(false)} title={t().me.avatar} visible={avatarSheet}>
        <ChoiceList
          onSelect={(key) => {
            if (key === 'pick') void pick();
            else {
              setAvatarSheet(false);
              void avatar(() => removeAvatar(identity));
            }
          }}
          options={[{ key: 'pick', label: t().me.pickPhoto }, ...(me.data?.avatar_url ? [{ key: 'remove', label: t().me.removeAvatar }] : [])]}
          selected={null}
        />
      </Sheet>
    </PushedPage>
  );
}

/**
 * 修改密码 — assembly: checks the form, then changes the password through the session (the server
 * ends every other sign-in and gives this device new tokens). A wrong current password shows under
 * its field; the inputs stay on any failure.
 */
import { router, useFocusEffect } from 'expo-router';
import { useCallback, useRef, useState } from 'react';

import { errorCode } from '@/lib/api/errors';
import { session } from '@/lib/app-session';
import { passwordProblem } from '@/lib/me';
import { errorMessage } from '@/lib/user-error';
import { PasswordView } from '@/screens/MeView';
import { PushedPage } from '@/screens/PushedPage';
import { t } from '@/lib/i18n';
import { useLanguage } from '@/lib/interface-language';

type Problem = { field: 'current' | 'next' | 'confirm' | 'form'; message: string } | null;

export default function Password() {
  useLanguage(); // texts follow the interface language
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<Problem>(null);
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

  async function submit(current: string, next: string, confirm: string) {
    if (saving) return;
    const local = passwordProblem(current, next, confirm);
    if (local) return setProblem(local);
    setSaving(true);
    setProblem(null);
    try {
      await session.changePassword(current, next);
      if (visible.current) back();
    } catch (error) {
      setProblem(
        errorCode(error) === 'wrong_password'
          ? { field: 'current', message: t().me.wrongPassword }
          : { field: 'form', message: errorMessage(error) },
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <PushedPage onBack={back} title={t().me.changePassword}>
      <PasswordView
        onEdit={() => setProblem(null)}
        onSubmit={(c, n, f) => void submit(c, n, f)}
        problem={problem}
        saving={saving}
      />
    </PushedPage>
  );
}

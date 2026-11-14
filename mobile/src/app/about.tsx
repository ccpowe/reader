/** 关于 Reader — assembly: the app version and the server it is connected to. */
import Constants from 'expo-constants';
import { router } from 'expo-router';

import { useSession } from '@/lib/app-session';
import { AboutView } from '@/screens/MeView';
import { PushedPage } from '@/screens/PushedPage';
import { t } from '@/lib/i18n';
import { useLanguage } from '@/lib/interface-language';

export default function About() {
  useLanguage(); // texts follow the interface language
  const current = useSession();
  const server = current.status === 'signed-in' ? current.server.base.replace(/^https?:\/\//, '') : '';
  return (
    <PushedPage onBack={() => (router.canGoBack() ? router.back() : router.replace('/me'))} title={t().me.about}>
      <AboutView appVersion={Constants.expoConfig?.version ?? '—'} server={server} />
    </PushedPage>
  );
}

/** 关于 Reader — assembly: the app version and the server it is connected to. */
import Constants from 'expo-constants';
import { router } from 'expo-router';

import { useSession } from '@/lib/app-session';
import { AboutView } from '@/screens/MeView';
import { PushedPage } from '@/screens/PushedPage';

export default function About() {
  const current = useSession();
  const server = current.status === 'signed-in' ? current.server.base.replace(/^https?:\/\//, '') : '';
  return (
    <PushedPage onBack={() => (router.canGoBack() ? router.back() : router.replace('/me'))} title="关于 Reader">
      <AboutView appVersion={Constants.expoConfig?.version ?? '—'} server={server} />
    </PushedPage>
  );
}

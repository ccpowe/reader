/**
 * One subscription's feed, opened from 订阅 (a page of its own: back returns to 订阅, where it was
 * left). Assembly only: the subscription's name, the shared FeedPage, notices.
 */
import { onlineManager, useQuery } from '@tanstack/react-query';
import { router, useLocalSearchParams } from 'expo-router';
import { useCallback, useState, useSyncExternalStore } from 'react';
import Animated from 'react-native-reanimated';

import { FeedPage } from '@/components/FeedPage';
import { useIdentity } from '@/lib/app-session';
import { subscriptionsQuery } from '@/lib/queries/subscriptions';
import { displayAddress } from '@/lib/subscriptions';
import { SubscriptionFeedView } from '@/screens/SubscriptionsView';
import { EmptyState, Toast } from '@/ui';
import { duration } from '@/ui/theme';
import { t } from '@/lib/i18n';
import { useLanguage } from '@/lib/interface-language';

const toastLife = {
  animationName: { '0%': { opacity: 0 }, '4%': { opacity: 1 }, '94%': { opacity: 1 }, '100%': { opacity: 0 } },
  animationDuration: `${duration.toast}ms`,
  animationFillMode: 'both',
} as const;

function useOnline(): boolean {
  return useSyncExternalStore(onlineManager.subscribe.bind(onlineManager), () => onlineManager.isOnline());
}

export default function SubscriptionFeed() {
  useLanguage(); // texts follow the interface language
  const { id = '' } = useLocalSearchParams<{ id: string }>();
  const identity = useIdentity();
  const online = useOnline();
  const subscriptions = useQuery(subscriptionsQuery(identity));
  const subscription = subscriptions.data?.find((s) => s.id === id);
  const gone = subscriptions.data !== undefined && !subscription;
  const [notice, setNotice] = useState<{ id: number; message: string } | null>(null);
  const showNotice = useCallback((message: string) => setNotice((current) => ({ id: (current?.id ?? 0) + 1, message })), []);
  const back = () => (router.canGoBack() ? router.back() : router.replace('/subscriptions'));

  return (
    <SubscriptionFeedView
      banner={online ? null : t().common.offline}
      onBack={back}
      title={subscription ? (subscription.name ?? displayAddress(subscription.url)) : ''}
    >
      {gone ? (
        <EmptyState description={t().subs.goneHint} title={t().subs.gone} />
      ) : (
        <FeedPage active identity={identity} onNotice={showNotice} scope={{ kind: 'subscription', id }} />
      )}
      {notice ? (
        <Animated.View className="absolute bottom-6 left-0 right-0" key={notice.id} pointerEvents="none" style={toastLife}>
          <Toast message={notice.message} />
        </Animated.View>
      ) : null}
    </SubscriptionFeedView>
  );
}

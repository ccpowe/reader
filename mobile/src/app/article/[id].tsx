/**
 * Reader page for an article of a subscription — assembly: reads the article and hands it to the
 * Reader (src/components/Reader.tsx) with saving through /saved.
 */
import { useQuery } from '@tanstack/react-query';
import { router, useLocalSearchParams } from 'expo-router';
import { View } from 'react-native';

import { Reader, type ReaderDoc } from '@/components/Reader';
import type { ArticleDetail } from '@/lib/api/schema.gen';
import { useIdentity } from '@/lib/app-session';
import { articleQuery } from '@/lib/queries/article';
import { setSaved, UnknownOutcome } from '@/lib/queries/today';
import { safeHttpUrl } from '@/lib/reader';
import { errorMessage } from '@/lib/user-error';
import { ArticleView } from '@/screens/ArticleView';
import { Button, EmptyState, Skeleton } from '@/ui';
import { t } from '@/lib/i18n';
import { useLanguage } from '@/lib/interface-language';

function docOf(article: ArticleDetail, url: string): ReaderDoc {
  return {
    url,
    title: article.title,
    displayTitle: article.title_translation?.text ?? article.title,
    kind: article.kind,
    sourceType: article.source?.type,
    sourceName: article.source?.name,
    author: article.author,
    publishedAt: article.published_at ?? article.first_seen_at,
    imageUrl: article.image_url,
    bodyHtml: article.body_html,
    summary: article.summary,
    saved: article.is_saved,
  };
}

export default function ArticleRoute() {
  useLanguage(); // texts follow the interface language
  const { id } = useLocalSearchParams<{ id: string }>();
  const identity = useIdentity();
  const article = useQuery(articleQuery(identity, id));
  if (article.data) {
    const url = safeHttpUrl(article.data.url);
    if (url) {
      const current = article.data;
      const toggleSave = async () => {
        try {
          await setSaved(identity, current.id, !current.is_saved);
          return null;
        } catch (error) {
          return error instanceof UnknownOutcome
            ? t().common.bookmarkUnknown
            : t().common.bookmarkFailed(current.is_saved, errorMessage(error));
        }
      };
      return <Reader doc={docOf(current, url)} key={id} onToggleSave={toggleSave} />;
    }
    return (
      <ArticleView onBack={() => router.back()} saved={article.data.is_saved}>
        <EmptyState description={t().common.unsafeAddress} title={t().common.unsafeAddressTitle} />
      </ArticleView>
    );
  }
  return (
    <ArticleView onBack={() => router.back()} saved={false}>
      {article.isError ? (
        <EmptyState
          action={<Button onPress={() => void article.refetch()} title={t().common.retry} />}
          description={errorMessage(article.error)}
          title={t().reader.openFailedTitle}
        />
      ) : (
        <View className="gap-4 px-6 py-6">
          <Skeleton lines={2} />
          <Skeleton lines={6} />
        </View>
      )}
    </ArticleView>
  );
}

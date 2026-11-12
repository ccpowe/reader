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
            ? '没收到服务器的确认，已重新加载收藏状态。'
            : `${current.is_saved ? '取消收藏' : '收藏'}没有成功：${errorMessage(error)}`;
        }
      };
      return <Reader doc={docOf(current, url)} key={id} onToggleSave={toggleSave} />;
    }
    return (
      <ArticleView onBack={() => router.back()} saved={article.data.is_saved}>
        <EmptyState description="它的地址不是网页地址，为了安全不打开。" title="这条内容无法打开" />
      </ArticleView>
    );
  }
  return (
    <ArticleView onBack={() => router.back()} saved={false}>
      {article.isError ? (
        <EmptyState
          action={<Button onPress={() => void article.refetch()} title="重试" />}
          description={errorMessage(article.error)}
          title="没能打开"
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

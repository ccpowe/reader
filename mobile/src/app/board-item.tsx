/**
 * Reader page for a board entry (P29) — assembly: finds the entry in the board it was opened from
 * and hands it to the Reader (src/components/Reader.tsx), saving through 「从榜单收藏」. HN entries
 * get the 讨论区 tool. An entry no longer in the board still opens by its address.
 */
import { useQuery } from '@tanstack/react-query';
import { router, useLocalSearchParams } from 'expo-router';
import { View } from 'react-native';

import { Reader, type ReaderDoc } from '@/components/Reader';
import type { BoardEntry } from '@/lib/api/schema.gen';
import { errorCode } from '@/lib/api/errors';
import { useIdentity } from '@/lib/app-session';
import { BOARDS, type BoardKey, type BoardView } from '@/lib/boards';
import { boardQuery, boardTranslationsQuery, setBoardSaved, translationPreferenceQuery } from '@/lib/queries/boards';
import { UnknownOutcome } from '@/lib/queries/today';
import { safeHttpUrl } from '@/lib/reader';
import { errorMessage } from '@/lib/user-error';
import { ArticleView } from '@/screens/ArticleView';
import { EmptyState, Skeleton } from '@/ui';
import { t } from '@/lib/i18n';
import { useLanguage } from '@/lib/interface-language';

function docOf(view: BoardView, url: string, entry: BoardEntry | undefined, translatedTitle: string | undefined): ReaderDoc {
  const title = entry?.title ?? url;
  return {
    url,
    title,
    displayTitle: entry?.title_translation?.text ?? translatedTitle ?? title,
    // Reddit posts open as their page (P16); other boards by address (YouTube links get the player).
    sourceType: view.board === 'reddit' ? 'reddit' : null,
    sourceName: BOARDS.find((b) => b.key === view.board)?.label,
    author: entry?.author,
    publishedAt: entry?.published_at,
    imageUrl: entry?.image_urls?.[0],
    summary: entry?.description,
    saved: entry?.is_saved ?? false,
    discussionUrl: view.board === 'hn' ? entry?.discussion_url : null,
  };
}

export default function BoardItemRoute() {
  useLanguage(); // texts follow the interface language
  const params = useLocalSearchParams<{ board: BoardKey; variant: string; subreddit: string; url: string }>();
  const identity = useIdentity();
  const view: BoardView = { board: params.board, variant: params.variant ?? '', subreddit: params.subreddit || null };
  // The board's own link identifies the entry (lookup, saving); the checked one is what opens.
  const link = params.url ?? '';
  const url = safeHttpUrl(link);
  const board = useQuery({ ...boardQuery(identity, view), enabled: url !== null });
  const preference = useQuery(translationPreferenceQuery(identity));
  const translations = useQuery(boardTranslationsQuery(identity, view, board.data, preference.data));
  const translated = preference.data?.enabled ? translations.data : undefined;

  if (!url) {
    return (
      <ArticleView onBack={() => router.back()} saved={false}>
        <EmptyState description={t().common.unsafeAddress} title={t().common.unsafeAddressTitle} />
      </ArticleView>
    );
  }
  if (board.data === undefined && board.isPending && board.fetchStatus !== 'idle') {
    return (
      <ArticleView onBack={() => router.back()} saved={false}>
        <View className="gap-4 px-6 py-6">
          <Skeleton lines={2} />
          <Skeleton lines={6} />
        </View>
      </ArticleView>
    );
  }
  const entry = board.data?.items.find((item) => item.url === link);
  const doc = docOf(view, url, entry, entry ? translated?.titles.get(entry.title) : undefined);

  async function toggleSave(): Promise<string | null> {
    try {
      await setBoardSaved(identity, view, link, !doc.saved);
      return null;
    } catch (error) {
      if (error instanceof UnknownOutcome) return t().common.bookmarkUnknown;
      if (errorCode(error) === 'board_item_not_found') return t().boards.gone;
      return t().common.bookmarkFailed(doc.saved, errorMessage(error));
    }
  }

  return <Reader doc={doc} key={url} onToggleSave={toggleSave} />;
}

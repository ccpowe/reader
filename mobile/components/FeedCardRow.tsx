import { memo, useCallback } from 'react';

import type { XFeedCardTranslation } from '../domain/xFeedTranslation';
import type { FeedItem } from '../lib/api';
import { ArticleCard } from './FeedCards';

export const FeedCardRow = memo(function FeedCardRow({
  avatarAccessToken,
  item,
  onOpenArticle,
  onRetryXTranslation,
  onToggleSave,
  xTranslation,
}: {
  avatarAccessToken: string;
  item: FeedItem;
  onOpenArticle: (item: FeedItem) => void;
  onRetryXTranslation: (item: FeedItem) => void;
  onToggleSave: (item: FeedItem) => void | Promise<void>;
  xTranslation?: XFeedCardTranslation;
}) {
  const handlePress = useCallback(() => onOpenArticle(item), [item, onOpenArticle]);
  const handleRetryXTranslation = useCallback(
    () => onRetryXTranslation(item),
    [item, onRetryXTranslation],
  );
  const handleToggleSave = useCallback(() => onToggleSave(item), [item, onToggleSave]);

  return (
    <ArticleCard
      avatarAccessToken={avatarAccessToken}
      item={item}
      onPress={handlePress}
      onRetryXTranslation={handleRetryXTranslation}
      onToggleSave={handleToggleSave}
      xTranslation={xTranslation}
    />
  );
});

/** One article's detail (react-guidelines §4): the reader page's data. */
import { queryOptions } from '@tanstack/react-query';

import type { ArticleDetail } from '../api/schema.gen';
import { session } from '../app-session';
import { key } from '../query';

export function articleQuery(identity: string, id: string) {
  return queryOptions({
    queryKey: key(identity, 'article', id),
    queryFn: ({ signal }) => session.api().request<ArticleDetail>(`/articles/${id}`, { signal }),
    staleTime: 5 * 60_000,
  });
}

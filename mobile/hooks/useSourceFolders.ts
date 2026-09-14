import { useMemo } from 'react';
import { sourceFolder, sourceFolders } from '../domain/folders';
import type { SourceListItem } from '../lib/api';

/** Retain category indices across OS-language changes and source-status refreshes. */
export function useSourceFolders(sources: readonly Pick<SourceListItem, 'folder_name'>[]) {
  // The key uses locale-independent string order. Hermes can
  // change localeCompare behavior even when its locale argument stays 'zh-CN'.
  const folderSetKey = JSON.stringify([...new Set(sources.map(sourceFolder))].sort());
  return useMemo(() => sourceFolders(
    (JSON.parse(folderSetKey) as string[]).map((folder_name) => ({ folder_name })),
  ), [folderSetKey]);
}

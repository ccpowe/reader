import type { SourceListItem } from '../lib/api';
import { i18n } from '../i18n';

/** Stable internal value used when a subscription has no assigned folder. */
export const UNCATEGORIZED_FOLDER = '__uncategorized__';

export type SourceFolder = string;

/**
 * Converts API folder values into the non-empty value used by the UI.
 * Keeping this separate makes folder selection and persistence easy to test.
 */
export function normalizeFolderName(folderName: string | null | undefined): SourceFolder {
  return folderName?.trim() || UNCATEGORIZED_FOLDER;
}

export function sourceFolder(source: Pick<SourceListItem, 'folder_name'>): SourceFolder {
  return normalizeFolderName(source.folder_name);
}

export function sourceFolders(sources: readonly Pick<SourceListItem, 'folder_name'>[]): SourceFolder[] {
  return Array.from(new Set(sources.map(sourceFolder))).sort((left, right) => {
    if (left === UNCATEGORIZED_FOLDER) return 1;
    if (right === UNCATEGORIZED_FOLDER) return -1;
    // CategoryPager retains pages by index; interface changes must keep their
    // order stable so a mounted feed never changes its category unexpectedly.
    return left.localeCompare(right, 'zh-CN');
  });
}

export function folderLabel(folder: SourceFolder, locale = i18n.resolvedLanguage ?? i18n.language): string {
  return folder === UNCATEGORIZED_FOLDER ? i18n.t('common:uncategorized', { lng: locale }) : folder;
}

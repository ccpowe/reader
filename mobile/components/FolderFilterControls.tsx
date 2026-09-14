import { ScrollView, StyleSheet, View } from 'react-native';
import { SearchField } from './SearchField';

import { folderLabel } from '../domain/folders';
import { colors } from '../ui/tokens';
import { Chip } from './Chip';
import { useTranslation } from '../i18n';

export const FOLDER_FILTER_CONTROLS_HEIGHT = 110;
export const FOLDER_FILTER_CONTENT_GAP = 8;

export function FolderFilterControls({
  active = true,
  onSearchFocusChange,
  folders,
  onChangeQuery,
  onSelectFolder,
  query,
  searchAccessibilityLabel,
  searchPlaceholder,
  selectedFolder,
}: {
  active?: boolean;
  onSearchFocusChange?: (focused: boolean) => void;
  folders: string[];
  onChangeQuery: (query: string) => void;
  onSelectFolder: (folder: string | null) => void;
  query: string;
  searchAccessibilityLabel: string;
  searchPlaceholder: string;
  selectedFolder: string | null;
}) {
  const { t } = useTranslation('common');
  return (
    <View style={styles.controls}>
      <SearchField active={active} onFocusChange={onSearchFocusChange} label={searchAccessibilityLabel} placeholder={searchPlaceholder} value={query} onChangeText={onChangeQuery} />
      <ScrollView
        contentContainerStyle={styles.filters}
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.chipScroller}
      >
        <Chip active={selectedFolder === null} label={t('all')} onPress={() => onSelectFolder(null)} />
        {folders.map((folder) => (
          <Chip
            active={selectedFolder === folder}
            key={folder}
            label={folderLabel(folder)}
            onPress={() => onSelectFolder(folder)}
          />
        ))}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  controls: { height: FOLDER_FILTER_CONTROLS_HEIGHT, paddingHorizontal: 24, paddingTop: 13 },
  chipScroller: { marginTop: 13, borderBottomWidth: 1, borderBottomColor: colors.border, flexGrow: 0, height: 42 },
  filters: { alignItems: 'center', flexDirection: 'row', gap: 21, paddingRight: 20 },
});

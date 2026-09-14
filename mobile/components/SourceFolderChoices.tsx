import { ScrollView, StyleSheet } from 'react-native';

import { UNCATEGORIZED_FOLDER } from '../domain/folders';
import { Chip } from './Chip';
import { useTranslation } from '../i18n';

export function SourceFolderChoices({
  folders,
  newFolder,
  onSelect,
  selected,
}: {
  folders: string[];
  newFolder: string;
  onSelect: (folder: string) => void;
  selected: string;
}) {
  const { t } = useTranslation('common');
  return (
    <ScrollView contentContainerStyle={styles.folderChoices} horizontal showsHorizontalScrollIndicator={false}>
      <Chip active={selected === UNCATEGORIZED_FOLDER && !newFolder.trim()} label={t('uncategorized')} onPress={() => onSelect(UNCATEGORIZED_FOLDER)} />
      {folders
        .filter((folder) => folder !== UNCATEGORIZED_FOLDER)
        .map((folder) => (
          <Chip active={selected === folder && !newFolder.trim()} key={folder} label={folder} onPress={() => onSelect(folder)} />
        ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  folderChoices: { alignItems: 'center', flexDirection: 'row', gap: 8, marginBottom: 12, paddingRight: 20 },
});

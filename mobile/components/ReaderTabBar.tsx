import { Pressable, StyleSheet, View } from 'react-native';
import { Feather } from '@expo/vector-icons';
import Reanimated, { useAnimatedStyle, type SharedValue } from 'react-native-reanimated';
import { CollapsibleChrome } from './CollapsibleChrome';
import { colors } from '../ui/tokens';
import { READER_TAB_BAR_HEIGHT } from '../ui/layout';
import { useTranslation } from '../i18n';

export type ReaderTab = 'inbox' | 'explore' | 'sources' | 'saved' | 'settings';
const tabs: [ReaderTab, keyof typeof Feather.glyphMap, string][] = [['inbox', 'home', 'tabToday'], ['explore', 'bar-chart', 'tabRankings'], ['sources', 'rss', 'tabSources'], ['saved', 'bookmark', 'tabSaved'], ['settings', 'user', 'tabProfile']];
export function ReaderTabBar({ activeTab, hidden, onChange }: { activeTab: ReaderTab; hidden: boolean; onChange: (tab: ReaderTab) => void }) { return <CollapsibleChrome height={READER_TAB_BAR_HEIGHT} hidden={hidden}><Bar activeTab={activeTab} onChange={onChange} /></CollapsibleChrome>; }
export function MotionReaderTabBar({ activeTab, onChange, progress }: { activeTab: ReaderTab; onChange: (tab: ReaderTab) => void; progress: SharedValue<number> }) { const animationStyle = useAnimatedStyle(() => ({ transform: [{ translateY: READER_TAB_BAR_HEIGHT * progress.value }] })); return <Reanimated.View style={[styles.motion, animationStyle]}><Bar activeTab={activeTab} onChange={onChange} /></Reanimated.View>; }
function Bar({ activeTab, onChange }: { activeTab: ReaderTab; onChange: (tab: ReaderTab) => void }) {
  const { t } = useTranslation('common');
  return (
    <View style={styles.bar}>
      {tabs.map(([tab, icon, label]) => {
        const selected = activeTab === tab;
        return (
          <Pressable
            accessibilityLabel={t(label)}
            accessibilityRole="tab"
            accessibilityState={{ selected }}
            key={tab}
            onPress={() => onChange(tab)}
            style={styles.item}
            testID={`tab_${tab}`}
          >
            <Feather color={selected ? colors.textStrong : colors.textMuted} name={icon} size={22} />
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  bar: { backgroundColor: colors.background, borderTopColor: colors.border, borderTopWidth: 1, flexDirection: 'row', height: READER_TAB_BAR_HEIGHT, paddingVertical: 2, paddingHorizontal: 8 },
  motion: { bottom: 0, left: 0, position: 'absolute', right: 0, zIndex: 20 },
  item: { alignItems: 'center', flex: 1, justifyContent: 'center' },
});

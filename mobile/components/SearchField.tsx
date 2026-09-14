import { useEffect, useRef } from 'react';
import { Keyboard, Pressable, StyleSheet, TextInput, View } from 'react-native';
import { Feather } from '@expo/vector-icons';
import { useTranslation } from '../i18n';

export function SearchField({ label, placeholder, value, onChangeText, onFocusChange, active = true }: {
  active?: boolean; label: string; placeholder: string; value: string; onChangeText: (value: string) => void; onFocusChange?: (focused: boolean) => void;
}) {
  const { t } = useTranslation('common');
  const input = useRef<TextInput>(null);
  useEffect(() => {
    const subscription = Keyboard.addListener('keyboardDidHide', () => {
      if (input.current?.isFocused()) {
        input.current.blur();
        onFocusChange?.(false);
      }
    });
    return () => subscription.remove();
  }, [onFocusChange]);
  useEffect(() => {
    if (!active) { input.current?.blur(); onFocusChange?.(false); }
  }, [active, onFocusChange]);
  return <View style={styles.field}>
    <Feather name="search" size={17} color="#91948A" />
    <TextInput ref={input} onFocus={() => onFocusChange?.(true)} onBlur={() => onFocusChange?.(false)} accessibilityLabel={label} autoCapitalize="none" autoCorrect={false} placeholder={placeholder} placeholderTextColor="#95988F" value={value} onChangeText={onChangeText} style={styles.input} />
    {value ? <Pressable accessibilityRole="button" accessibilityLabel={t('clearSearch')} onPress={() => onChangeText('')} style={styles.clear}><Feather name="x" size={17} color="#777B74" /></Pressable> : null}
  </View>;
}
const styles = StyleSheet.create({
  field: { alignItems: 'center', flexDirection: 'row', gap: 8, backgroundColor: '#F5F4EF', borderRadius: 10, paddingHorizontal: 12, height: 42 },
  input: { flex: 1, color: '#242924', fontSize: 13, paddingVertical: 0, minHeight: 42 },
  clear: { height: 42, width: 32, marginRight: -8, alignItems: 'center', justifyContent: 'center' },
});

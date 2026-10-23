import { useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Feather } from '@expo/vector-icons';

import { isProductHuntDate, productHuntMonthGrid, shiftProductHuntDate, shiftProductHuntMonth } from '../domain/productHuntDate';
import { useTranslation } from '../i18n';
import { colors, radii, spacing, touchTarget } from '../ui/tokens';
import { BottomSheetModal } from './BottomSheetModal';

type Props = {
  visible: boolean;
  selectedDate: string;
  today: string;
  onSelectDate: (date: string) => void;
  onClose: () => void;
};

function utcDay(date: string): Date {
  const value = new Date(0);
  value.setUTCFullYear(Number(date.slice(0, 4)), Number(date.slice(5, 7)) - 1, Number(date.slice(8, 10)));
  return value;
}

export function ProductHuntDatePicker({ visible, selectedDate, today, onSelectDate, onClose }: Props) {
  const { t, i18n } = useTranslation('feed');
  const [month, setMonth] = useState(selectedDate.slice(0, 7));
  useEffect(() => {
    if (visible) setMonth(selectedDate.slice(0, 7));
  }, [visible, selectedDate]);

  const language = i18n.resolvedLanguage ?? i18n.language;
  const monthLabel = new Intl.DateTimeFormat(language, { year: 'numeric', month: 'long', timeZone: 'UTC' }).format(utcDay(`${month}-01`));
  const dayLabel = new Intl.DateTimeFormat(language, { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long', timeZone: 'UTC' });
  const weekdayLabel = new Intl.DateTimeFormat(language, { weekday: 'short', timeZone: 'UTC' });
  const weekdays = Array.from({ length: 7 }, (_, index) => weekdayLabel.format(utcDay(`2026-03-${String(index + 1).padStart(2, '0')}`)));
  const { leadingDays, daysInMonth } = productHuntMonthGrid(month);
  const cells = Array.from({ length: Math.ceil((leadingDays + daysInMonth) / 7) * 7 }, (_, index) => index - leadingDays + 1);
  const yesterday = shiftProductHuntDate(today, -1);
  const canGoBack = month !== '0001-01';
  const canGoForward = month < today.slice(0, 7);
  const select = (date: string) => {
    if (!isProductHuntDate(date, today)) return;
    onSelectDate(date);
    onClose();
  };

  return (
    <BottomSheetModal contentStyle={styles.sheet} onClose={onClose} visible={visible}>
      <View style={styles.content}>
        <View style={styles.handle} />
        <View style={styles.heading}>
          <Text accessibilityRole="header" style={styles.title}>{t('productHuntChooseDate')}</Text>
          <Pressable accessibilityLabel={t('productHuntCloseDatePicker')} accessibilityRole="button" onPress={onClose} style={styles.iconButton}>
            <Feather name="x" size={20} color={colors.textMuted} />
          </Pressable>
        </View>
        <View style={styles.shortcuts}>
          <Pressable accessibilityRole="button" onPress={() => select(today)} style={styles.shortcut}>
            <Text style={styles.shortcutText}>{t('productHuntToday')}</Text>
          </Pressable>
          <Pressable accessibilityRole="button" onPress={() => select(yesterday)} style={styles.shortcut}>
            <Text style={styles.shortcutText}>{t('productHuntYesterday')}</Text>
          </Pressable>
        </View>
        <View style={styles.monthHeading}>
          <Pressable accessibilityLabel={t('productHuntPreviousMonth')} accessibilityRole="button" accessibilityState={{ disabled: !canGoBack }} disabled={!canGoBack} onPress={() => setMonth(shiftProductHuntMonth(month, -1))} style={styles.iconButton}>
            <Feather name="chevron-left" size={22} color={canGoBack ? colors.textStrong : colors.borderStrong} />
          </Pressable>
          <Text style={styles.monthTitle}>{monthLabel}</Text>
          <Pressable accessibilityLabel={t('productHuntNextMonth')} accessibilityRole="button" accessibilityState={{ disabled: !canGoForward }} disabled={!canGoForward} onPress={() => setMonth(shiftProductHuntMonth(month, 1))} style={styles.iconButton}>
            <Feather name="chevron-right" size={22} color={canGoForward ? colors.textStrong : colors.borderStrong} />
          </Pressable>
        </View>
        <View style={styles.grid}>
          {weekdays.map((label, index) => <View key={index} style={styles.cell}><Text style={styles.weekday}>{label}</Text></View>)}
          {cells.map((day, index) => {
            if (day < 1 || day > daysInMonth) return <View key={index} style={styles.cell} />;
            const date = `${month}-${String(day).padStart(2, '0')}`;
            const disabled = date > today;
            const selected = date === selectedDate;
            return (
              <View key={index} style={styles.cell}>
                <Pressable
                  accessibilityLabel={dayLabel.format(utcDay(date))}
                  accessibilityRole="button"
                  accessibilityState={{ disabled, selected }}
                  disabled={disabled}
                  onPress={() => select(date)}
                  style={({ pressed }) => [styles.dayButton, selected && styles.selectedDay, pressed && !disabled && styles.pressed]}
                >
                  <Text style={[styles.dayText, date === today && !selected && styles.todayText, disabled && styles.disabledText, selected && styles.selectedText]}>{day}</Text>
                </Pressable>
              </View>
            );
          })}
        </View>
      </View>
    </BottomSheetModal>
  );
}

const styles = StyleSheet.create({
  sheet: { backgroundColor: colors.background, paddingHorizontal: 6 },
  content: { alignSelf: 'center', maxWidth: 420, width: '100%' },
  handle: { alignSelf: 'center', backgroundColor: colors.borderStrong, borderRadius: 2, height: 4, marginBottom: spacing.lg, width: 32 },
  heading: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.sm },
  title: { color: colors.textStrong, fontSize: 22, fontWeight: '700' },
  iconButton: { alignItems: 'center', height: touchTarget, justifyContent: 'center', width: touchTarget },
  shortcuts: { flexDirection: 'row', gap: spacing.lg, marginBottom: spacing.sm },
  shortcut: { justifyContent: 'center', minHeight: touchTarget, paddingHorizontal: spacing.xs },
  shortcutText: { color: colors.textSecondary, fontSize: 14, fontWeight: '600' },
  monthHeading: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.xs },
  monthTitle: { color: colors.textStrong, fontSize: 16, fontWeight: '600' },
  grid: { flexDirection: 'row', flexWrap: 'wrap' },
  cell: { alignItems: 'center', justifyContent: 'center', width: '14.285714%' },
  weekday: { color: colors.textMuted, fontSize: 12, paddingVertical: 10, textAlign: 'center' },
  dayButton: { alignItems: 'center', borderRadius: radii.pill, height: touchTarget, justifyContent: 'center', width: touchTarget },
  selectedDay: { backgroundColor: colors.textStrong },
  dayText: { color: colors.textStrong, fontSize: 14 },
  todayText: { color: colors.textPrimary, fontWeight: '700' },
  disabledText: { color: colors.borderStrong },
  selectedText: { color: colors.surface, fontWeight: '700' },
  pressed: { opacity: 0.65 },
});

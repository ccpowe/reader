import { useMemo, useRef, useState } from 'react';
import { ActivityIndicator, PanResponder, Pressable, StyleSheet, View } from 'react-native';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import { clampToolPosition, draggedToolPosition, readToolPosition, saveToolPosition } from '../domain/readerToolPosition';
import { colors } from '../ui/tokens';
import { useTranslation } from '../i18n';

export function FloatingReaderTools({ translationActive, translationBusy = false, onTranslate, translationLabel, modeIcon, modeLabel, modeBusy = false, onSwitchMode }: {
  translationActive: boolean;
  translationBusy?: boolean;
  onTranslate: () => void;
  translationLabel?: string;
  modeIcon?: 'web' | 'book-open-page-variant' | 'play-box-outline';
  modeLabel?: string;
  modeBusy?: boolean;
  onSwitchMode?: () => void;
}) {
  const { t } = useTranslation('common');
  const [position, setPosition] = useState(readToolPosition);
  const [height, setHeight] = useState(0);
  const [dragging, setDragging] = useState(false);
  const groupHeight = onSwitchMode && modeIcon ? 92 : 50;
  const travel = Math.max(0, height - groupHeight);
  const live = useRef({ position, travel });
  live.current = { position, travel };
  const start = useRef(position);
  const suppressPressUntil = useRef(0);
  const responder = useMemo(() => {
    const finish = () => {
      setDragging(false);
      suppressPressUntil.current = Date.now() + 250;
      saveToolPosition(live.current.position);
    };
    return PanResponder.create({
      onMoveShouldSetPanResponderCapture: (_, gesture) => Math.abs(gesture.dy) > 6 && Math.abs(gesture.dy) >= Math.abs(gesture.dx),
      onPanResponderGrant: () => { start.current = live.current.position; setDragging(true); },
      onPanResponderMove: (_, gesture) => {
        const next = draggedToolPosition(start.current, gesture.dy, live.current.travel);
        live.current.position = next;
        setPosition(next);
      },
      onPanResponderRelease: finish,
      onPanResponderTerminate: finish,
      onPanResponderTerminationRequest: () => false,
    });
  }, []);
  function press(action: () => void) {
    if (Date.now() >= suppressPressUntil.current) action();
  }
  function moveAccessibly(delta: number) {
    const next = clampToolPosition(position + delta);
    setPosition(next);
    saveToolPosition(next);
  }
  return (
    <View onLayout={(event) => setHeight(event.nativeEvent.layout.height)} pointerEvents="box-none" style={styles.layer}>
      <View {...responder.panHandlers} pointerEvents="box-none" style={[styles.tools, { top: position * travel }, dragging && styles.dragging]}>
        <Pressable
          accessibilityActions={[{ name: 'increment', label: t('moveToolsDown') }, { name: 'decrement', label: t('moveToolsUp') }]}
          accessibilityHint={t('dragToolsHint')}
          accessibilityLabel={translationLabel ?? t('toggleBilingual')}
          accessibilityRole="button"
          accessibilityState={{ selected: translationActive, busy: translationBusy }}
          onAccessibilityAction={(event) => moveAccessibly(event.nativeEvent.actionName === 'increment' ? 0.1 : -0.1)}
          onPress={() => press(onTranslate)}
          style={({ pressed }) => [styles.button, translationActive && styles.active, pressed && styles.pressed]}
        >
          {translationBusy ? <ActivityIndicator color={colors.textPrimary} size="small" /> : <MaterialCommunityIcons color={colors.textPrimary} name="translate" size={20} />}
        </Pressable>
        {onSwitchMode && modeIcon ? (
          <Pressable accessibilityHint={t('dragToolsHint')} accessibilityLabel={modeLabel ?? t('switchPageMode')} accessibilityRole="button" accessibilityState={{ busy: modeBusy }} onPress={() => press(onSwitchMode)} style={({ pressed }) => [styles.button, pressed && styles.pressed]}>
            {modeBusy ? <ActivityIndicator color={colors.textPrimary} size="small" /> : <MaterialCommunityIcons color={colors.textPrimary} name={modeIcon} size={20} />}
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  layer: { bottom: 12, left: 0, position: 'absolute', right: 0, top: 68, zIndex: 12 },
  tools: { backgroundColor: 'rgba(255,255,255,0.94)', borderColor: '#e7e7e0', borderRadius: 23, borderWidth: 1, elevation: 1, padding: 3, position: 'absolute', right: 8, shadowColor: '#21281c', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.03, shadowRadius: 4, width: 46 },
  button: { alignItems: 'center', borderRadius: 19, height: 42, justifyContent: 'center', width: 38 },
  active: { backgroundColor: '#efefea' },
  pressed: { opacity: 0.7 },
  dragging: { opacity: 0.85 },
});

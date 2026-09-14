import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Animated, StyleSheet, View } from 'react-native';

/** Keeps layout stable while screen chrome animates fully off-screen. */
export function CollapsibleChrome({ children, height, hidden }: { children: ReactNode; height: number; hidden: boolean }) {
  const translateY = useRef(new Animated.Value(hidden ? -height : 0)).current;
  const [occupiesSpace, setOccupiesSpace] = useState(!hidden);
  const hasMounted = useRef(false);
  useEffect(() => {
    if (!hasMounted.current) { hasMounted.current = true; translateY.setValue(hidden ? -height : 0); return; }
    translateY.stopAnimation();
    if (hidden) {
      Animated.timing(translateY, { duration: 180, toValue: -height, useNativeDriver: true }).start(({ finished }) => { if (finished) setOccupiesSpace(false); });
      return;
    }
    setOccupiesSpace(true);
    translateY.setValue(-height);
    const frame = requestAnimationFrame(() => Animated.timing(translateY, { duration: 180, toValue: 0, useNativeDriver: true }).start());
    return () => cancelAnimationFrame(frame);
  }, [height, hidden, translateY]);
  return <View pointerEvents={hidden ? 'none' : 'auto'} style={[styles.clip, { height: occupiesSpace ? height : 0 }]}><Animated.View style={{ height, transform: [{ translateY }] }}>{children}</Animated.View></View>;
}

const styles = StyleSheet.create({ clip: { flexShrink: 0, overflow: 'hidden' } });

import type { ReactNode } from 'react';
import { Modal, Pressable, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Text } from './Text';

/**
 * qp-sheet: mobile bottom sheet (raised surface, top corners --radius-2xl, handle). quietpaper has
 * no captured motion for it; it fades in (qp-fade), as the style pack's CSS does.
 */
export function Sheet({
  visible,
  title,
  onClose,
  children,
}: {
  visible: boolean;
  title?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const insets = useSafeAreaInsets();
  return (
    <Modal animationType="fade" onRequestClose={onClose} transparent visible={visible}>
      <Pressable accessibilityLabel="关闭" className="flex-1 bg-overlay" onPress={onClose} />
      <SheetBody bottomInset={insets.bottom} title={title}>
        {children}
      </SheetBody>
    </Modal>
  );
}

/** The sheet panel alone, for previews and stories. */
export function SheetBody({ title, children, bottomInset = 0 }: { title?: string; children: ReactNode; bottomInset?: number }) {
  return (
    <View className="rounded-t-2xl bg-raised px-4 pt-2 shadow-lg" style={{ paddingBottom: 16 + bottomInset }}>
      <View className="mb-2 h-1 w-9 self-center rounded-full bg-control" />
      {title ? (
        <Text className="mb-2" level="section">
          {title}
        </Text>
      ) : null}
      {children}
    </View>
  );
}

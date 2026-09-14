import type { ReactNode } from 'react';
import {
  KeyboardAvoidingView,
  Modal,
  Platform,
  ScrollView,
  StyleSheet,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

/** A keyboard-safe, small-screen-safe bottom sheet shared by form modals. */
export function BottomSheetModal({
  children,
  constrainHeight = true,
  contentStyle,
  onClose,
  visible,
}: {
  children: ReactNode;
  constrainHeight?: boolean;
  contentStyle?: StyleProp<ViewStyle>;
  onClose: () => void;
  visible: boolean;
}) {
  const insets = useSafeAreaInsets();

  return (
    <Modal animationType="slide" onRequestClose={onClose} transparent visible={visible}>
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        style={styles.keyboardFrame}
      >
        <View style={styles.backdrop}>
          <ScrollView
            bounces={false}
            contentContainerStyle={styles.scrollContent}
            keyboardDismissMode="interactive"
            keyboardShouldPersistTaps="handled"
            showsVerticalScrollIndicator={false}
            style={!constrainHeight ? { marginTop: insets.top + 12 } : undefined}
          >
            <View
              style={[
                styles.sheet,
                constrainHeight && styles.heightLimit,
                { paddingBottom: Math.max(20, insets.bottom + 12) },
                contentStyle,
              ]}
            >
              {children}
            </View>
          </ScrollView>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { backgroundColor: 'rgba(17,17,17,0.28)', flex: 1 },
  keyboardFrame: { flex: 1 },
  scrollContent: { flexGrow: 1, justifyContent: 'flex-end' },
  sheet: {
    backgroundColor: '#FAFAF8',
    borderTopLeftRadius: 22,
    borderTopRightRadius: 22,
    padding: 20,
  },
  heightLimit: { maxHeight: '92%' },
});

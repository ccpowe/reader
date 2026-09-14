import { useAvatarImageSource } from '../hooks/useAvatarImageSource';
import { useEffect, useState } from 'react';
import { Image, StyleSheet, View } from 'react-native';
import { MaterialCommunityIcons } from '@expo/vector-icons';

import { type SourceListItem } from '../lib/api';
import { sourceIcon } from '../domain/source';
import { colors } from '../ui/tokens';

export function SourceAvatar({
  accessToken,
  size = 42,
  source,
}: {
  accessToken: string;
  size?: number;
  source: Pick<SourceListItem, 'avatar_url' | 'kind'>;
}) {
  const imageSource = useAvatarImageSource(source.avatar_url, accessToken);
  const [failed, setFailed] = useState(false);
  const shape = { borderRadius: Math.min(12, size * 0.24), height: size, width: size };

  useEffect(() => setFailed(false), [accessToken, source.avatar_url]);

  return (
    <View style={[styles.avatar, shape]}>
      {imageSource && !failed ? (
        <Image
          accessible={false}
          fadeDuration={0}
          onError={() => setFailed(true)}
          resizeMethod="resize"
          resizeMode="cover"
          source={imageSource}
          style={shape}
        />
      ) : (
        <MaterialCommunityIcons
          color={colors.textStrong}
          name={sourceIcon(source.kind)}
          size={Math.max(20, Math.round(size * 0.52))}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  avatar: {
    alignItems: 'center',
    backgroundColor: colors.surfaceMuted,
    justifyContent: 'center',
    overflow: 'hidden',
  },
});

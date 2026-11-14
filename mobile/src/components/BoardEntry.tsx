/**
 * One board entry, old app frame (P26: checked against the device): a two-digit rank, the title
 * (translated when available) in regular weight, then what the board has — description, domain
 * or author, a stats line (HN: ▲ points · comments; GitHub: contributors, language, stars,
 * forks; Product Hunt: ▲ votes · comments; TechTwitter: topics, quality, age), and a thumbnail on the right
 * when there is one. No row bookmark: saving is on the entry page.
 */
import { IconGitFork, IconStar, IconTriangle } from '@tabler/icons-react-native';
import { Image, Pressable, View } from 'react-native';

import type { BoardRow } from '@/lib/boards';
import { Avatar, Icon, Text } from '@/ui';
import { t } from '@/lib/i18n';

export type BoardEntryItem = Pick<BoardRow, 'rank' | 'title'> &
  Partial<
    Pick<
      BoardRow,
      'description' | 'byline' | 'score' | 'comments' | 'language' | 'stars' | 'forks' | 'tags' | 'quality' | 'age' | 'imageUrl'
    >
  > & {
    contributors?: { name: string; avatarUrl?: string | null }[];
  };

function Stat({ icon, value }: { icon?: typeof IconStar; value: string }) {
  return (
    <View className="flex-row items-center gap-1">
      {icon ? <Icon icon={icon} size="xs" /> : null}
      <Text level="caption" style={{ fontVariant: ['tabular-nums'] }} tone="secondary">
        {value}
      </Text>
    </View>
  );
}

function Stats({ entry }: { entry: BoardEntryItem }) {
  const contributors = entry.contributors ?? [];
  const parts = [
    entry.score ? <Stat icon={IconTriangle} key="score" value={entry.score} /> : null,
    entry.comments ? <Stat key="comments" value={t().boards.comments(entry.comments)} /> : null,
    entry.language ? <Stat key="language" value={entry.language} /> : null,
    entry.stars ? <Stat icon={IconStar} key="stars" value={entry.stars} /> : null,
    entry.forks ? <Stat icon={IconGitFork} key="forks" value={entry.forks} /> : null,
    ...(entry.tags ?? []).map((tag) => <Stat key={`#${tag}`} value={`#${tag}`} />),
    entry.quality ? <Stat key="quality" value={entry.quality} /> : null,
    entry.age ? <Stat key="age" value={entry.age} /> : null,
  ].filter(Boolean);
  if (contributors.length === 0 && parts.length === 0) return null;
  return (
    <View className="flex-row flex-wrap items-center gap-x-3 gap-y-1">
      {contributors.length > 0 ? (
        <View className="flex-row">
          {contributors.slice(0, 3).map((person, index) => (
            <View
              className={index > 0 ? '-ml-1.5 rounded-full border-2 border-canvas' : 'rounded-full border-2 border-canvas'}
              key={person.name}
            >
              <Avatar name={person.name} size={20} uri={person.avatarUrl} />
            </View>
          ))}
        </View>
      ) : null}
      {parts}
    </View>
  );
}

export function BoardEntry({ entry, onOpen }: { entry: BoardEntryItem; onOpen?: () => void }) {
  return (
    <Pressable accessibilityRole="button" className="active:bg-pressed" onPress={onOpen}>
      <View className="mx-6 flex-row gap-2 border-b border-subtle py-6">
        <Text className="w-8" level="card-plain" style={{ fontVariant: ['tabular-nums'] }}>
          {String(entry.rank).padStart(2, '0')}
        </Text>
        <View className="min-w-0 flex-1 gap-2">
          <Text level="card-plain" numberOfLines={3}>
            {entry.title}
          </Text>
          {entry.description ? (
            <Text level="read" numberOfLines={3} tone="secondary">
              {entry.description}
            </Text>
          ) : null}
          {entry.byline ? (
            <Text level="caption" numberOfLines={1} tone="secondary">
              {entry.byline}
            </Text>
          ) : null}
          <Stats entry={entry} />
        </View>
        {entry.imageUrl ? (
          <Image className="ml-2 mt-1 rounded-lg bg-sunken" source={{ uri: entry.imageUrl }} style={{ width: 88, height: 88 }} />
        ) : null}
      </View>
    </Pressable>
  );
}

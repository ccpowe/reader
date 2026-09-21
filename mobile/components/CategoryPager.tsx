import { useEffect, useRef, useState, type ReactNode } from 'react';
import { View, type StyleProp, type ViewStyle } from 'react-native';
import PagerView from 'react-native-pager-view';

/** Native horizontal pager with a guard against Android page-selection loops. */
export function CategoryPager<T>({
  onPageTransitionStart,
  options,
  pageStyle,
  renderPage,
  selected,
  style,
  onSelect,
}: {
  onPageTransitionStart?: () => void;
  options: readonly T[];
  pageStyle: StyleProp<ViewStyle>;
  renderPage: (option: T) => ReactNode;
  selected: T;
  style: StyleProp<ViewStyle>;
  onSelect: (option: T) => void;
}) {
  const pagerRef = useRef<PagerView>(null);
  const selectedIndex = Math.max(0, options.indexOf(selected));
  const nativePageIndex = useRef(selectedIndex);
  const [renderCenter, setRenderCenter] = useState(selectedIndex);

  useEffect(() => {
    if (nativePageIndex.current === selectedIndex) return;
    nativePageIndex.current = selectedIndex;
    setRenderCenter(selectedIndex);
    pagerRef.current?.setPage(selectedIndex);
  }, [selectedIndex]);

  return <PagerView
    initialPage={selectedIndex}
    offscreenPageLimit={1}
    onPageScrollStateChanged={(event) => {
      if (event.nativeEvent.pageScrollState === 'dragging') onPageTransitionStart?.();
    }}
    onPageSelected={(event) => {
      const nextIndex = event.nativeEvent.position;
      setRenderCenter(nextIndex);
      if (nativePageIndex.current === nextIndex) return;
      nativePageIndex.current = nextIndex;
      const option = options[nextIndex];
      if (option !== undefined && !Object.is(option, selected)) onSelect(option);
    }}
    ref={pagerRef}
    style={style}
  >
    {options.map((option, index) => <View collapsable={false} key={`category-${index}`} style={pageStyle}>
      {Math.abs(index - renderCenter) <= 1 ? renderPage(option) : null}
    </View>)}
  </PagerView>;
}

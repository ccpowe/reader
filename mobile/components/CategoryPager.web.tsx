import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  ScrollView,
  View,
  type NativeScrollEvent,
  type NativeSyntheticEvent,
  type StyleProp,
  type ViewStyle,
} from 'react-native';

/** Web equivalent of the native pager; keeps tab selection and swipe state in sync. */
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
  const pagerRef = useRef<ScrollView>(null);
  const nativePageIndex = useRef(Math.max(0, options.indexOf(selected)));
  const positionedWidth = useRef(0);
  const [pageWidth, setPageWidth] = useState(0);
  const selectedIndex = Math.max(0, options.indexOf(selected));
  const [renderCenter, setRenderCenter] = useState(selectedIndex);

  useEffect(() => {
    if (pageWidth <= 0) return;
    const widthChanged = positionedWidth.current !== pageWidth;
    if (!widthChanged && nativePageIndex.current === selectedIndex) return;
    nativePageIndex.current = selectedIndex;
    setRenderCenter(selectedIndex);
    positionedWidth.current = pageWidth;
    pagerRef.current?.scrollTo({ animated: !widthChanged, x: selectedIndex * pageWidth });
  }, [pageWidth, selectedIndex]);

  function handleScrollEnd(event: NativeSyntheticEvent<NativeScrollEvent>) {
    if (pageWidth <= 0 || !options.length) return;
    const nextIndex = Math.min(
      options.length - 1,
      Math.max(0, Math.round(event.nativeEvent.contentOffset.x / pageWidth)),
    );
    setRenderCenter(nextIndex);
    if (nativePageIndex.current === nextIndex) return;
    nativePageIndex.current = nextIndex;
    const option = options[nextIndex];
    if (option !== undefined && !Object.is(option, selected)) onSelect(option);
  }

  return (
    <ScrollView
      contentContainerStyle={{ flexGrow: 1 }}
      horizontal
      onLayout={(event) => {
        const nextWidth = event.nativeEvent.layout.width;
        if (nextWidth > 0 && nextWidth !== pageWidth) setPageWidth(nextWidth);
      }}
      onMomentumScrollEnd={handleScrollEnd}
      onScroll={(event) => {
        if (pageWidth <= 0 || !options.length) return;
        const center = Math.min(
          options.length - 1,
          Math.max(0, Math.round(event.nativeEvent.contentOffset.x / pageWidth)),
        );
        setRenderCenter((current) => current === center ? current : center);
      }}
      scrollEventThrottle={16}
      onScrollBeginDrag={onPageTransitionStart}
      pagingEnabled
      ref={pagerRef}
      showsHorizontalScrollIndicator={false}
      style={style}
    >
      {options.map((option, index) => (
        <View
          collapsable={false}
          key={`category-${index}`}
          style={[pageStyle, { width: pageWidth || '100%' }]}
        >
          {Math.abs(index - renderCenter) <= 1 ? renderPage(option) : null}
        </View>
      ))}
    </ScrollView>
  );
}

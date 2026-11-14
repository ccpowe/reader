/** Layout decisions only. Never move or restyle host nodes to create room. */
export function webTranslationPresentation(source: Element, options: {
  wholeBlock: boolean;
  internalTranslation: boolean;
  constrainedPlacement: boolean;
  typographySource?: Element;
  text: string;
  translatedOnly: boolean;
}) {
  const view = source.ownerDocument.defaultView!;
  const sourceCss = view.getComputedStyle(source);
  // A visual block can contain a styled inline heading or a deeper projected
  // run. Text styling belongs to that range, not necessarily the outer block.
  const textSource = options.typographySource ?? source;
  const css = view.getComputedStyle(textSource);
  // display:contents ancestors do not create layout items. Shadow hosts do.
  const layoutParent = (element: Element): Element | null => (element as HTMLElement).assignedSlot
    ?? element.parentElement ?? (element.getRootNode() as ShadowRoot).host ?? null;
  let parent = layoutParent(source);
  for (let depth = 0; parent && depth < 8 && view.getComputedStyle(parent).display === 'contents'; depth++) parent = layoutParent(parent);
  const parentDisplay = parent ? view.getComputedStyle(parent).display : '';
  const layoutItem = /flex|grid/.test(parentDisplay);
  const heading = /^(H[1-6])$/.test(textSource.tagName) || textSource.getAttribute('role') === 'heading';
  const cell = /^(LI|TD|TH|DT|DD)$/.test(textSource.tagName);
  const constrained = Number.parseInt(sourceCss.getPropertyValue('-webkit-line-clamp'), 10) > 0 || sourceCss.whiteSpace === 'nowrap';
  const canContain = !/flex|grid|table|contents/.test(sourceCss.display) && !/^(IMG|INPUT|BR|HR|IFRAME|VIDEO|CANVAS|SVG)$/.test(source.tagName);
  const inside = options.internalTranslation || (options.wholeBlock && canContain && (layoutItem || constrained || options.constrainedPlacement));
  const inline = !heading && !cell && !layoutItem && /^inline(?:-block)?$/.test(css.display)
    && options.text.length <= 60 && options.text.trim().split(/\s+/).length <= 10 && !options.text.includes('\n');
  const fontSize = Number.parseFloat(css.fontSize) || 16;
  const top = inline || options.translatedOnly ? 0 : fontSize * (heading ? 0.15 : cell ? 0.2 : 0.25);
  const bottom = inside || inline ? 0 : Math.min(fontSize, Math.max(fontSize * 0.35, Number.parseFloat(css.marginBottom) || 0));
  const styles: [string, string][] = ['color', 'font-family', 'font-size', 'font-style', 'font-weight', 'line-height', 'letter-spacing', 'text-align', 'direction']
    .map(property => [property, css.getPropertyValue(property)]);
  styles.push(['display', inline ? 'inline' : 'block'], ['margin-top', `${top}px`], ['margin-bottom', `${bottom}px`]);
  for (const side of ['left', 'right']) {
    styles.push([`margin-${side}`, options.wholeBlock && !inside && !inline ? sourceCss.getPropertyValue(`margin-${side}`) : '0px']);
    styles.push([`padding-${side}`, options.wholeBlock && !inside && !inline ? sourceCss.getPropertyValue(`padding-${side}`) : '0px']);
  }
  if (inline && !options.translatedOnly) styles.push([css.direction === 'rtl' ? 'margin-right' : 'margin-left', `${fontSize * 0.5}px`]);
  return { inside, inline, styles: [...new Map(styles)] };
}

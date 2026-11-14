/**
 * react-native-webview 14.0.1's index.d.ts declares `class WebView<P = undefined>` with props
 * `WebViewProps & P`; the default makes every prop `never` in JSX. Same component, usable types.
 */
import type { ComponentClass } from 'react';
import { WebView as UntypedWebView, type WebViewProps } from 'react-native-webview';

export type WebViewHandle = InstanceType<typeof UntypedWebView<object>>;

export const WebView = UntypedWebView as unknown as ComponentClass<WebViewProps> & (new (props: WebViewProps) => WebViewHandle);

import { fetch as expoFetch } from 'expo/fetch';

// React Native's legacy fetch does not reliably implement redirect: 'error'.
// Expo's native transport disables redirects before sending another request.
export const readerFetch: typeof fetch = expoFetch as typeof fetch;

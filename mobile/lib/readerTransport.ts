/** Browser/Node transport. Native uses Expo's redirect-aware implementation. */
export const readerFetch: typeof fetch = (input, init) => fetch(input, init);

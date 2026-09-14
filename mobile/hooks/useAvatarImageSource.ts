import { useQuery } from '@tanstack/react-query';
import { avatarImageSource, fetchWithTimeout } from '../lib/api';
import { useReaderRuntime } from '../lib/connection/react';
import { getReaderSessionEpochSnapshot } from '../lib/connection/snapshot';

function encodeBase64(bytes: Uint8Array): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  const chunks: string[] = [];
  // Do not rely on browser-only btoa or a Node Buffer polyfill on native.
  for (let start = 0; start < bytes.length; start += 8_190) {
    let chunk = '';
    for (let index = start; index < Math.min(start + 8_190, bytes.length); index += 3) {
      const value = (bytes[index] << 16) | ((bytes[index + 1] ?? 0) << 8) | (bytes[index + 2] ?? 0);
      chunk += alphabet[(value >>> 18) & 63] + alphabet[(value >>> 12) & 63] +
        (index + 1 < bytes.length ? alphabet[(value >>> 6) & 63] : '=') +
        (index + 2 < bytes.length ? alphabet[value & 63] : '=');
    }
    chunks.push(chunk);
  }
  return chunks.join('');
}

/** Fetch protected images through the redirect-safe transport before displaying. */
export function useAvatarImageSource(uri: string | null | undefined, accessToken: string) {
  const runtime = useReaderRuntime();
  const source = uri ? avatarImageSource(uri, accessToken)[0] : null;
  const protectedImage = Boolean(source?.headers);
  const query = useQuery({
    queryKey: ['reader', runtime?.identity.server_id, runtime?.generation, getReaderSessionEpochSnapshot(), 'avatar-image', uri],
    enabled: Boolean(uri && protectedImage),
    staleTime: 60 * 60_000,
    gcTime: 5 * 60_000,
    retry: false,
    queryFn: async ({ signal }) => {
      let dataUri = '';
      const response = await fetchWithTimeout(uri!, { headers: source!.headers, signal }, runtime?.generation, async value => {
        if (!value.ok) throw new Error('Avatar unavailable');
        const body = new Uint8Array(await value.arrayBuffer());
        if (body.length > 5 * 1024 * 1024) throw new Error('Avatar is too large');
        const mime = value.headers.get('content-type')?.split(';')[0].trim().toLowerCase();
        // Match the raster formats served by the backend, including site favicons.
        if (!mime || !/^image\/(jpeg|png|webp|gif|avif|x-icon|vnd\.microsoft\.icon)$/.test(mime)) throw new Error('Invalid avatar');
        dataUri = `data:${mime};base64,${encodeBase64(body)}`;
      });
      if (!response.ok) throw new Error('Avatar unavailable');
      return dataUri;
    },
  });
  const displayed = protectedImage ? query.data : uri;
  return displayed ? [{ uri: displayed }] : undefined;
}

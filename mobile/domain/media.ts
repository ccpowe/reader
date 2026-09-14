export const YOUTUBE_APP_REFERER = 'https://com.ccpower.reader';

export function youtubeVideoId(url: string): string | null {
  try {
    const parsed = new URL(url);
    if (parsed.hostname === 'youtu.be') return parsed.pathname.slice(1) || null;
    if (parsed.pathname.startsWith('/shorts/') || parsed.pathname.startsWith('/embed/')) {
      return parsed.pathname.split('/')[2] || null;
    }
    return parsed.searchParams.get('v');
  } catch {
    return null;
  }
}

export function youtubeEmbedUrl(videoId: string): string {
  const safeId = videoId.replace(/[^A-Za-z0-9_-]/g, '');
  const parameters = new URLSearchParams({
    cc_load_policy: '1',
    iv_load_policy: '3',
    origin: YOUTUBE_APP_REFERER,
    playsinline: '1',
    rel: '0',
  });
  return `https://www.youtube-nocookie.com/embed/${safeId}?${parameters.toString()}`;
}

export function youtubeWatchUrl(videoId: string): string {
  const safeId = videoId.replace(/[^A-Za-z0-9_-]/g, '');
  return `https://www.youtube.com/watch?v=${safeId}`;
}

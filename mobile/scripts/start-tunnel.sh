#!/bin/sh
set -eu

MOBILE_EXPO_HOST="${READER_EXPO_HOST:-tunnel}"
case "$MOBILE_EXPO_HOST" in
  lan|localhost|tunnel) ;;
  *)
    echo "invalid READER_EXPO_HOST: $MOBILE_EXPO_HOST" >&2
    exit 1
    ;;
esac
# Connection coordinates are entered and discovered inside the app.  This
# launcher only starts Expo and deliberately does not inject a static API or
# Supabase configuration into the bundle.
exec env EXPO_NO_DOTENV=1 pnpm run start -- --host "$MOBILE_EXPO_HOST" --port "${PASEO_PORT:?PASEO_PORT is required}"

import { useEffect, useState } from 'react';

/** `flag`, but turning on only after it has stayed on for `ms` (--delay-loading). Off is immediate. */
export function useDelayed(flag: boolean, ms: number) {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (!flag) {
      setShown(false);
      return;
    }
    const timer = setTimeout(() => setShown(true), ms);
    return () => clearTimeout(timer);
  }, [flag, ms]);
  return flag && shown;
}

import { useState, useEffect } from "react";

/**
 * Debounce a value by the given delay (ms).
 * Returns the debounced value that only updates after the delay has passed
 * since the last change.
 */
export default function useDebounce<T>(value: T, delayMs: number = 300): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}

import { useEffect, useCallback } from "react";

export interface ShortcutHandlers {
  onSearch?: () => void;
  onSave?: () => void;
  onEscape?: () => void;
}

export default function useKeyboardShortcuts(handlers: ShortcutHandlers) {
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;

      // Cmd/Ctrl + K — open search
      if (mod && e.key === "k") {
        e.preventDefault();
        handlers.onSearch?.();
        return;
      }

      // Cmd/Ctrl + S — save
      if (mod && e.key === "s") {
        e.preventDefault();
        handlers.onSave?.();
        return;
      }

      // Escape — close modals/panels
      if (e.key === "Escape") {
        handlers.onEscape?.();
        return;
      }
    },
    [handlers]
  );

  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);
}

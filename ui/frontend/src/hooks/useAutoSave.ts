import { useState, useEffect, useRef, useCallback } from "react";

type SaveStatus = "saved" | "saving" | "unsaved";

interface UseAutoSaveOptions {
  /** Debounce delay before auto-save triggers (ms) */
  delayMs?: number;
  /** The save function to call */
  onSave: () => Promise<void>;
  /** Whether auto-save is enabled */
  enabled?: boolean;
}

interface UseAutoSaveReturn {
  saveStatus: SaveStatus;
  setSaveStatus: (status: SaveStatus) => void;
  /** Mark content as changed (triggers debounced auto-save) */
  markDirty: () => void;
  /** Force immediate save */
  saveNow: () => Promise<void>;
  /** Status label for display */
  statusLabel: string;
  /** Status color for display */
  statusColor: string;
}

export default function useAutoSave({
  delayMs = 1500,
  onSave,
  enabled = true,
}: UseAutoSaveOptions): UseAutoSaveReturn {
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("saved");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onSaveRef = useRef(onSave);
  onSaveRef.current = onSave;

  const doSave = useCallback(async () => {
    setSaveStatus("saving");
    try {
      await onSaveRef.current();
      setSaveStatus("saved");
    } catch {
      setSaveStatus("unsaved");
    }
  }, []);

  const markDirty = useCallback(() => {
    if (!enabled) return;
    setSaveStatus("unsaved");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(doSave, delayMs);
  }, [enabled, delayMs, doSave]);

  const saveNow = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    await doSave();
  }, [doSave]);

  // Warn before unload if unsaved
  useEffect(() => {
    if (saveStatus !== "unsaved") return;
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [saveStatus]);

  // Cleanup timer on unmount
  useEffect(() => {
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, []);

  const statusLabel = saveStatus === "saved" ? "已保存" : saveStatus === "saving" ? "保存中..." : "未保存";
  const statusColor = saveStatus === "saved"
    ? "var(--jade)"
    : saveStatus === "saving"
      ? "var(--gold)"
      : "var(--error)";

  return { saveStatus, setSaveStatus, markDirty, saveNow, statusLabel, statusColor };
}

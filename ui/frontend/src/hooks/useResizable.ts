import { useState, useCallback, useRef, useEffect } from "react";

interface UseResizableOptions {
  direction: "horizontal" | "vertical";
  initialSize: number;
  minSize: number;
  maxSize: number;
  /** CSS cursor during drag */
  cursor?: string;
  /** Called on every resize frame */
  onResize?: (size: number) => void;
  /** Called when drag ends */
  onResizeEnd?: (size: number) => void;
}

interface UseResizableReturn {
  size: number;
  isDragging: boolean;
  handleProps: {
    onMouseDown: (e: React.MouseEvent) => void;
    onTouchStart: (e: React.TouchEvent) => void;
  };
  setSize: (s: number) => void;
}

export function useResizable(opts: UseResizableOptions): UseResizableReturn {
  const { direction, initialSize, minSize, maxSize, cursor, onResize, onResizeEnd } = opts;

  const [size, setSize] = useState(initialSize);
  const [isDragging, setIsDragging] = useState(false);
  const startPos = useRef(0);
  const startSize = useRef(0);

  const clamp = useCallback((v: number) => Math.max(minSize, Math.min(maxSize, v)), [minSize, maxSize]);

  const onMove = useCallback(
    (clientX: number, clientY: number) => {
      const delta = direction === "horizontal" ? clientX - startPos.current : clientY - startPos.current;
      const next = clamp(startSize.current + delta);
      setSize(next);
      onResize?.(next);
    },
    [direction, clamp, onResize]
  );

  const onEnd = useCallback(() => {
    setIsDragging(false);
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    onResizeEnd?.(size);
  }, [size, onResizeEnd]);

  useEffect(() => {
    if (!isDragging) return;

    const handleMouseMove = (e: MouseEvent) => { e.preventDefault(); onMove(e.clientX, e.clientY); };
    const handleTouchMove = (e: TouchEvent) => { onMove(e.touches[0].clientX, e.touches[0].clientY); };
    const handleUp = () => onEnd();

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleUp);
    document.addEventListener("touchmove", handleTouchMove);
    document.addEventListener("touchend", handleUp);

    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleUp);
      document.removeEventListener("touchmove", handleTouchMove);
      document.removeEventListener("touchend", handleUp);
    };
  }, [isDragging, onMove, onEnd]);

  const startDrag = useCallback(
    (x: number, y: number) => {
      startPos.current = direction === "horizontal" ? x : y;
      startSize.current = size;
      setIsDragging(true);
      document.body.style.cursor = cursor ?? (direction === "horizontal" ? "col-resize" : "row-resize");
      document.body.style.userSelect = "none";
    },
    [direction, size, cursor]
  );

  const handleProps = {
    onMouseDown: (e: React.MouseEvent) => { e.preventDefault(); startDrag(e.clientX, e.clientY); },
    onTouchStart: (e: React.TouchEvent) => { startDrag(e.touches[0].clientX, e.touches[0].clientY); },
  };

  return { size, isDragging, handleProps, setSize };
}
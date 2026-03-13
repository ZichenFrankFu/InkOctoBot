import React, { useState, useRef, useEffect, useCallback } from "react";

interface Props {
  value: string[];
  onChange: (tags: string[]) => void;
  suggestions: string[];
  placeholder?: string;
  maxTags?: number;
}

export default function TagAutocomplete({ value, onChange, suggestions, placeholder, maxTags }: Props) {
  const [input, setInput] = useState("");
  const [showDropdown, setShowDropdown] = useState(false);
  const [highlightIdx, setHighlightIdx] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const filtered = input.trim()
    ? suggestions.filter(s => s.toLowerCase().includes(input.toLowerCase()) && !value.includes(s)).slice(0, 8)
    : suggestions.filter(s => !value.includes(s)).slice(0, 8);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const addTag = (tag: string) => {
    const t = tag.trim();
    if (!t || value.includes(t)) return;
    if (maxTags && value.length >= maxTags) return;
    onChange([...value, t]);
    setInput("");
    setHighlightIdx(-1);
    inputRef.current?.focus();
  };

  const removeTag = (tag: string) => {
    onChange(value.filter(t => t !== tag));
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (highlightIdx >= 0 && filtered[highlightIdx]) {
        addTag(filtered[highlightIdx]);
      } else if (input.trim()) {
        addTag(input.trim());
      }
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlightIdx(prev => Math.min(prev + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlightIdx(prev => Math.max(prev - 1, -1));
    } else if (e.key === "Backspace" && !input && value.length > 0) {
      removeTag(value[value.length - 1]);
    } else if (e.key === "Escape") {
      setShowDropdown(false);
    }
  };

  return (
    <div ref={containerRef} style={{ position: "relative" }}>
      <div style={{
        display: "flex", flexWrap: "wrap", gap: 4, padding: "4px 8px",
        background: "var(--bg-input)", border: "1px solid var(--border)",
        borderRadius: "var(--radius-sm)", minHeight: 34,
        transition: "border-color 0.15s",
        borderColor: showDropdown ? "var(--accent)" : undefined,
      }}>
        {value.map(tag => (
          <span key={tag} style={{
            display: "inline-flex", alignItems: "center", gap: 4,
            padding: "2px 8px", borderRadius: 12,
            background: "var(--accent-subtle)", color: "var(--accent)",
            fontSize: 11, fontWeight: 500,
          }}>
            {tag}
            <button onClick={() => removeTag(tag)} style={{
              background: "none", border: "none", color: "var(--accent)",
              cursor: "pointer", fontSize: 12, padding: 0, lineHeight: 1,
            }}>&times;</button>
          </span>
        ))}
        <input
          ref={inputRef}
          value={input}
          onChange={e => { setInput(e.target.value); setShowDropdown(true); setHighlightIdx(-1); }}
          onFocus={() => setShowDropdown(true)}
          onKeyDown={handleKeyDown}
          placeholder={value.length === 0 ? (placeholder || "输入标签...") : ""}
          style={{
            flex: 1, minWidth: 80, border: "none", background: "transparent",
            outline: "none", color: "var(--text-primary)", fontSize: 12,
            padding: "2px 0",
          }}
        />
      </div>

      {/* Dropdown */}
      {showDropdown && filtered.length > 0 && (
        <div style={{
          position: "absolute", top: "100%", left: 0, right: 0,
          zIndex: "var(--z-dropdown)" as any,
          background: "var(--bg-surface-2)", border: "1px solid var(--border)",
          borderRadius: "var(--radius-md)", padding: 4, marginTop: 2,
          boxShadow: "var(--shadow-lg)",
          maxHeight: 200, overflowY: "auto",
          animation: "slideUp 0.15s var(--ease-out)",
        }}>
          {filtered.map((s, i) => (
            <button key={s}
              onClick={() => addTag(s)}
              onMouseEnter={() => setHighlightIdx(i)}
              style={{
                display: "block", width: "100%", padding: "6px 12px",
                fontSize: 12, color: i === highlightIdx ? "var(--text-primary)" : "var(--text-secondary)",
                background: i === highlightIdx ? "var(--bg-surface-hover)" : "none",
                border: "none", borderRadius: "var(--radius-sm)",
                cursor: "pointer", textAlign: "left",
                transition: "all 0.1s",
              }}
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

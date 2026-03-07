import React, { useState, useEffect } from "react";

interface EntryData {
  entry_type: string;
  title: string;
  content: string;
  position_label?: string;
  user_notes?: string;
  user_rating?: number;
}

interface Props {
  entry?: EntryData;
  onSave: (data: EntryData) => void;
  onCancel: () => void;
}

const ENTRY_TYPES = [
  "scene",
  "character",
  "worldbuilding",
  "dialogue",
  "technique",
  "atmosphere",
  "plot_structure",
  "emotional_beat",
  "hook",
  "style_sample",
  "other",
] as const;

export default function EntryEditor({ entry, onSave, onCancel }: Props) {
  const [form, setForm] = useState<EntryData>({
    entry_type: "scene",
    title: "",
    content: "",
    position_label: "",
    user_notes: "",
    user_rating: 0,
  });

  useEffect(() => {
    if (entry) {
      setForm({
        entry_type: entry.entry_type,
        title: entry.title,
        content: entry.content,
        position_label: entry.position_label ?? "",
        user_notes: entry.user_notes ?? "",
        user_rating: entry.user_rating ?? 0,
      });
    }
  }, [entry]);

  const update = (field: keyof EntryData, value: string | number) => {
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="field">
        <label className="label">Entry Type</label>
        <select
          className="select w-full"
          value={form.entry_type}
          onChange={(e) => update("entry_type", e.target.value)}
        >
          {ENTRY_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label className="label">Title</label>
        <input
          className="input"
          value={form.title}
          onChange={(e) => update("title", e.target.value)}
          placeholder="Entry title"
        />
      </div>

      <div className="field">
        <label className="label">Content</label>
        <textarea
          className="input"
          rows={6}
          value={form.content}
          onChange={(e) => update("content", e.target.value)}
          placeholder="Entry content..."
        />
      </div>

      <div className="field">
        <label className="label">Position Label</label>
        <input
          className="input"
          value={form.position_label}
          onChange={(e) => update("position_label", e.target.value)}
          placeholder="e.g. Chapter 3, Scene 2"
        />
      </div>

      <div className="field">
        <label className="label">User Notes</label>
        <textarea
          className="input"
          rows={3}
          value={form.user_notes}
          onChange={(e) => update("user_notes", e.target.value)}
          placeholder="Your notes about this entry..."
        />
      </div>

      <div className="field">
        <label className="label">Rating</label>
        <select
          className="select"
          value={form.user_rating}
          onChange={(e) => update("user_rating", parseInt(e.target.value))}
        >
          <option value={0}>No rating</option>
          {[1, 2, 3, 4, 5].map((n) => (
            <option key={n} value={n}>
              {"\u2605".repeat(n)}
            </option>
          ))}
        </select>
      </div>

      <div className="flex gap-8" style={{ justifyContent: "flex-end", marginTop: 8 }}>
        <button className="btn-ghost" onClick={onCancel}>
          Cancel
        </button>
        <button
          className="btn-primary"
          onClick={() => onSave(form)}
          disabled={!form.title.trim() || !form.content.trim()}
        >
          Save
        </button>
      </div>
    </div>
  );
}

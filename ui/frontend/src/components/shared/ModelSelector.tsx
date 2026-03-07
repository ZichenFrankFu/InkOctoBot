import React, { useMemo } from "react";
import type { ModelProvider } from "../../api/types";

interface Props {
  value: { provider: string; model: string };
  onChange: (v: { provider: string; model: string }) => void;
  providers: ModelProvider[];
}

export default function ModelSelector({ value, onChange, providers }: Props) {
  const selectedProvider = useMemo(
    () => providers.find((p) => p.id === value.provider || p.name === value.provider),
    [providers, value.provider]
  );

  const models = useMemo(() => selectedProvider?.models || [], [selectedProvider]);

  const handleProviderChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const provId = e.target.value;
    const prov = providers.find((p) => p.id === provId);
    const firstModel = prov?.models?.[0] || "";
    onChange({ provider: provId, model: firstModel });
  };

  const handleModelChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    onChange({ ...value, model: e.target.value });
  };

  const selectStyle: React.CSSProperties = {
    padding: "6px 10px",
    border: "1px solid var(--border)",
    borderRadius: 4,
    fontSize: 12,
    background: "var(--bg-secondary)",
    color: "var(--text-primary)",
    outline: "none",
    minWidth: 120,
  };

  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <select value={value.provider} onChange={handleProviderChange} style={selectStyle}>
        <option value="">-- Provider --</option>
        {providers.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>

      <select
        value={value.model}
        onChange={handleModelChange}
        style={selectStyle}
        disabled={!value.provider}
      >
        <option value="">-- Model --</option>
        {models.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
    </div>
  );
}

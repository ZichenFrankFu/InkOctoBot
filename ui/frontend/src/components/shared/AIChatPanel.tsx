import React, { useState, useRef, useEffect, useCallback } from "react";
import { apiGet, apiPost, apiPut } from "../../api/client";

/** Render basic markdown (bold, italic, numbered/bullet lists, line breaks) to React elements */
function renderMarkdown(text: string): React.ReactNode[] {
  const lines = text.split("\n");
  const elements: React.ReactNode[] = [];
  const inlineFormat = (s: string, key: string): React.ReactNode => {
    const parts: React.ReactNode[] = [];
    let idx = 0;
    const re = /\*\*(.+?)\*\*|\*(.+?)\*/g;
    let match: RegExpExecArray | null;
    let last = 0;
    while ((match = re.exec(s)) !== null) {
      if (match.index > last) parts.push(s.slice(last, match.index));
      if (match[1]) parts.push(<strong key={`${key}-b${idx++}`}>{match[1]}</strong>);
      else if (match[2]) parts.push(<em key={`${key}-i${idx++}`}>{match[2]}</em>);
      last = re.lastIndex;
    }
    if (last < s.length) parts.push(s.slice(last));
    return parts.length === 1 ? parts[0] : <>{parts}</>;
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed) { elements.push(<br key={`br-${i}`} />); continue; }
    const numMatch = trimmed.match(/^(\d+)\.\s+(.*)$/);
    if (numMatch) {
      elements.push(<div key={`li-${i}`} style={{ display: "flex", gap: 6, marginLeft: 4 }}>
        <span style={{ color: "var(--text-tertiary)", flexShrink: 0 }}>{numMatch[1]}.</span>
        <span>{inlineFormat(numMatch[2], `il-${i}`)}</span>
      </div>);
      continue;
    }
    const bulletMatch = trimmed.match(/^[*\-]\s+(.*)$/);
    if (bulletMatch) {
      elements.push(<div key={`bl-${i}`} style={{ display: "flex", gap: 6, marginLeft: 8 }}>
        <span style={{ color: "var(--text-tertiary)", flexShrink: 0 }}>&bull;</span>
        <span>{inlineFormat(bulletMatch[1], `il-${i}`)}</span>
      </div>);
      continue;
    }
    elements.push(<div key={`p-${i}`}>{inlineFormat(trimmed, `il-${i}`)}</div>);
  }
  return elements;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  agentName?: string;
  agentId?: string;
  timestamp: number;
  status?: "thinking" | "speaking" | "done" | "error" | "aborted";
  followUpQuestion?: FollowUpData;
  canRegenerate?: boolean;
}

export interface FollowUpData {
  text: string;
  options: string[];
}

interface Props {
  messages: ChatMessage[];
  onSendMessage: (text: string) => void;
  onStopGeneration?: () => void;
  onRegenerateMessage?: (messageId: string) => void;
  onSelectFollowUpOption?: (messageId: string, option: string) => void;
  isGenerating?: boolean;
  placeholder?: string;
  /** Preserved input text when switching tabs */
  preservedInput?: string;
  onInputChange?: (text: string) => void;
  /** Clear chat callback */
  onClearHistory?: () => void;
  /** Delete single message callback */
  onDeleteMessage?: (messageId: string) => void;
  /** Compact mode for overlay panels */
  compact?: boolean;
  /** Header content (agent name/description) */
  headerContent?: React.ReactNode;
  /** Custom empty state */
  emptyState?: React.ReactNode;
  /** Quick prompts shown in empty state */
  quickPrompts?: string[];
  /** Template prompts shown as dropdown above input */
  templates?: { label: string; prompt: string }[];
}

const uid = () => `msg_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;

export default function AIChatPanel({
  messages, onSendMessage, onStopGeneration, onRegenerateMessage,
  onSelectFollowUpOption, isGenerating, placeholder, preservedInput,
  onInputChange, onClearHistory, onDeleteMessage, compact, headerContent, emptyState, quickPrompts,
  templates,
}: Props) {
  const [input, setInput] = useState(preservedInput || "");
  const [customFollowUp, setCustomFollowUp] = useState<{ msgId: string; text: string } | null>(null);
  const [showTemplates, setShowTemplates] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Sync preserved input
  useEffect(() => {
    if (preservedInput !== undefined && preservedInput !== input) {
      setInput(preservedInput);
    }
  }, [preservedInput]);

  // Notify parent of input changes
  useEffect(() => {
    onInputChange?.(input);
  }, [input]);

  // Auto-scroll on new messages
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = () => {
    if (!input.trim() || isGenerating) return;
    onSendMessage(input.trim());
    setInput("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const getAgentColor = (agentName?: string): { bg: string; border: string; text: string } => {
    const map: Record<string, { bg: string; border: string; text: string }> = {
      "Marketing": { bg: "var(--gold-subtle)", border: "var(--gold)", text: "var(--gold)" },
      "Story Architect": { bg: "var(--indigo-subtle)", border: "var(--indigo)", text: "var(--indigo)" },
      "Editor-Writer": { bg: "var(--jade-subtle)", border: "var(--jade)", text: "var(--jade)" },
      "EditAnalyzer": { bg: "var(--purple-subtle)", border: "var(--purple)", text: "var(--purple)" },
      "Scene Director": { bg: "var(--indigo-subtle)", border: "var(--indigo)", text: "var(--indigo)" },
      "Actor Agents": { bg: "var(--gold-subtle)", border: "var(--gold)", text: "var(--gold)" },
      "Evaluator": { bg: "var(--accent-subtle)", border: "var(--accent)", text: "var(--accent)" },
      "System": { bg: "var(--bg-surface-2)", border: "var(--text-tertiary)", text: "var(--text-tertiary)" },
    };
    return map[agentName || ""] || { bg: "var(--cyan-subtle)", border: "var(--cyan)", text: "var(--cyan)" };
  };

  return (
    <div className="ai-chat-panel" style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Header */}
      {headerContent && (
        <div className="ai-chat-header" style={{
          padding: compact ? "8px 12px" : "10px 14px",
          borderBottom: "1px solid var(--border)",
          display: "flex", alignItems: "center", justifyContent: "space-between",
          flexShrink: 0, background: "var(--bg-surface)",
        }}>
          {headerContent}
          {onClearHistory && (
            <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={onClearHistory}>
              清空对话
            </button>
          )}
        </div>
      )}

      {/* Messages area */}
      <div className="ai-chat-messages" style={{
        flex: 1, overflowY: "auto", padding: compact ? "8px 10px" : "12px 14px",
      }}>
        {messages.length === 0 && !isGenerating ? (
          <div>
            {emptyState || null}
            {quickPrompts && quickPrompts.length > 0 && (
              <div style={{ display: "grid", gap: 8, padding: emptyState ? "0 16px 16px" : "20px 16px" }}>
                {quickPrompts.map((hint, i) => (
                  <button key={i} onClick={() => setInput(hint)}
                    className="ai-chat-quick-prompt"
                    style={{
                      padding: "10px 14px", borderRadius: 8, border: "1px solid var(--border)",
                      background: "var(--bg-surface-2)", color: "var(--text-secondary)",
                      fontSize: 12, textAlign: "left", cursor: "pointer", lineHeight: 1.5,
                      transition: "all 0.15s",
                    }}
                    onMouseEnter={e => { e.currentTarget.style.borderColor = "var(--accent)"; e.currentTarget.style.background = "var(--accent-subtle)"; }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor = "var(--border)"; e.currentTarget.style.background = "var(--bg-surface-2)"; }}
                  >
                    {hint}
                  </button>
                ))}
              </div>
            )}
            {!emptyState && !quickPrompts?.length && (
              <div style={{ padding: "20px 16px" }} />
            )}
          </div>
        ) : (
          messages.map((msg) => {
            const isUser = msg.role === "user";
            const isSystem = msg.role === "system";
            const colors = getAgentColor(msg.agentName);

            return (
              <div key={msg.id} className="ai-chat-message" style={{
                display: "flex", gap: 10, padding: compact ? "6px 0" : "8px 0",
                flexDirection: isUser ? "row-reverse" : "row",
                animation: "slideUp 0.2s var(--ease-out)",
              }}>
                {/* Avatar */}
                <div style={{
                  width: 28, height: 28, borderRadius: "50%",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 13, flexShrink: 0,
                  background: isUser ? "var(--accent-subtle)" : isSystem ? "var(--bg-surface-2)" : colors.bg,
                  color: isUser ? "var(--accent)" : isSystem ? "var(--text-tertiary)" : colors.text,
                  border: `1px solid ${isUser ? "var(--accent)" : isSystem ? "var(--border)" : colors.border}`,
                }}>
                  {isUser ? "U" : isSystem ? "S" : (msg.agentName || "AI").charAt(0)}
                </div>

                {/* Bubble */}
                <div style={{ flex: 1, maxWidth: isUser ? "80%" : "90%", minWidth: 0 }}>
                  {/* Agent name label */}
                  {!isUser && msg.agentName && (
                    <div style={{
                      fontSize: 11, fontWeight: 600, marginBottom: 3,
                      color: isSystem ? "var(--text-tertiary)" : colors.text,
                      display: "flex", alignItems: "center", gap: 6,
                    }}>
                      {msg.agentName}
                      {msg.agentId && <span style={{ fontSize: 9, color: "var(--text-disabled)", fontWeight: 400 }}>#{msg.agentId}</span>}
                      {msg.status === "thinking" && <span className="loading-spinner" style={{ width: 10, height: 10 }} />}
                    </div>
                  )}

                  <div className={`ai-chat-bubble ${isUser ? "user" : ""}`} style={{
                    background: isUser ? "var(--accent-subtle)" : isSystem ? "var(--bg-surface-2)" : "var(--bg-surface)",
                    border: `1px solid ${isUser ? "rgba(224,85,69,0.2)" : "var(--border)"}`,
                    borderRadius: isUser ? "12px 4px 12px 12px" : "4px 12px 12px 12px",
                    padding: compact ? "8px 12px" : "10px 14px",
                    fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.6,
                    userSelect: "text", cursor: "text",
                  }}>
                    {isSystem ? (
                      <span style={{ fontStyle: "italic", color: "var(--text-tertiary)", fontSize: 12 }}>{msg.content}</span>
                    ) : (
                      renderMarkdown(msg.content)
                    )}
                  </div>

                  {/* Follow-up question */}
                  {msg.followUpQuestion && (
                    <div style={{
                      marginTop: 8, padding: "10px 12px", borderRadius: 10,
                      background: "var(--bg-surface)",
                      border: "1px solid var(--border)",
                    }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", marginBottom: 8, lineHeight: 1.6 }}>
                        {msg.followUpQuestion.text}
                      </div>
                      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                        {msg.followUpQuestion.options.map((opt, oi) => (
                          <button key={oi}
                            onClick={() => onSelectFollowUpOption?.(msg.id, opt)}
                            className="ai-chat-follow-up-option"
                            style={{
                              padding: "8px 14px", borderRadius: 8,
                              border: "1px solid var(--border)",
                              background: "var(--bg-surface-2)",
                              color: "var(--text-primary)",
                              fontSize: 12, textAlign: "left", cursor: "pointer",
                              transition: "all 0.15s", lineHeight: 1.5,
                            }}
                            onMouseEnter={e => { e.currentTarget.style.borderColor = "var(--accent)"; e.currentTarget.style.background = "var(--accent-subtle)"; }}
                            onMouseLeave={e => { e.currentTarget.style.borderColor = "var(--border)"; e.currentTarget.style.background = "var(--bg-surface-2)"; }}
                          >
                            {opt}
                          </button>
                        ))}
                        {/* Custom input option */}
                        {customFollowUp?.msgId === msg.id ? (
                          <div style={{ display: "flex", gap: 6 }}>
                            <input className="input" value={customFollowUp.text}
                              onChange={e => setCustomFollowUp({ msgId: msg.id, text: e.target.value })}
                              onKeyDown={e => {
                                if (e.key === "Enter" && customFollowUp.text.trim()) {
                                  onSelectFollowUpOption?.(msg.id, customFollowUp.text.trim());
                                  setCustomFollowUp(null);
                                }
                              }}
                              placeholder="输入你的想法..." autoFocus
                              style={{ flex: 1, fontSize: 12 }} />
                            <button className="btn-primary" style={{ fontSize: 11, padding: "4px 12px", flexShrink: 0 }}
                              onClick={() => {
                                if (customFollowUp.text.trim()) {
                                  onSelectFollowUpOption?.(msg.id, customFollowUp.text.trim());
                                  setCustomFollowUp(null);
                                }
                              }}
                              disabled={!customFollowUp.text.trim()}>确认</button>
                            <button className="btn" style={{ fontSize: 11, padding: "4px 10px", flexShrink: 0 }}
                              onClick={() => setCustomFollowUp(null)}>取消</button>
                          </div>
                        ) : (
                          <button
                            onClick={() => setCustomFollowUp({ msgId: msg.id, text: "" })}
                            style={{
                              padding: "8px 14px", borderRadius: 8,
                              border: "1px dashed var(--border)",
                              background: "transparent",
                              color: "var(--text-tertiary)",
                              fontSize: 12, textAlign: "left", cursor: "pointer",
                              transition: "all 0.15s",
                            }}
                          >
                            其他（自由输入）
                          </button>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Actions: regenerate + delete */}
                  {((!isUser && msg.status === "done" && msg.canRegenerate && onRegenerateMessage) || onDeleteMessage) && (
                    <div style={{ marginTop: 4, display: "flex", gap: 4 }}>
                      {!isUser && msg.status === "done" && msg.canRegenerate && onRegenerateMessage && (
                        <button className="btn-ghost" style={{ fontSize: 11, padding: "2px 8px", color: "var(--text-disabled)" }}
                          onClick={() => onRegenerateMessage(msg.id)}>
                          重新生成
                        </button>
                      )}
                      {onDeleteMessage && (
                        <button className="btn-ghost" style={{ fontSize: 11, padding: "2px 8px", color: "var(--text-disabled)" }}
                          onClick={() => onDeleteMessage(msg.id)}>
                          删除
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })
        )}

        {/* Generating indicator */}
        {isGenerating && messages.length > 0 && messages[messages.length - 1]?.status !== "thinking" && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 0", color: "var(--text-tertiary)", fontSize: 12 }}>
            <div className="loading-spinner" style={{ width: 14, height: 14 }} />
            生成中...
          </div>
        )}

        <div ref={chatEndRef} />
      </div>

      {/* Input area */}
      <div style={{
        padding: compact ? "8px 10px" : "10px 14px",
        borderTop: "1px solid var(--border)",
        background: "var(--bg-surface)",
        flexShrink: 0,
        position: "relative",
      }}>
        {/* Template dropdown */}
        {showTemplates && templates && templates.length > 0 && (
          <div style={{
            position: "absolute", bottom: "100%", left: 0, right: 0,
            background: "var(--bg-surface)", border: "1px solid var(--border)",
            borderBottom: "none", borderRadius: "var(--radius-sm) var(--radius-sm) 0 0",
            maxHeight: 200, overflowY: "auto", boxShadow: "var(--shadow-md)",
            zIndex: 10,
          }}>
            <div style={{ padding: "6px 12px", fontSize: 10, fontWeight: 600, color: "var(--text-tertiary)", borderBottom: "1px solid var(--border)" }}>
              选择模板
            </div>
            {templates.map((t, i) => (
              <button key={i}
                onClick={() => { setInput(t.prompt); setShowTemplates(false); inputRef.current?.focus(); }}
                style={{
                  display: "block", width: "100%", textAlign: "left", padding: "8px 12px",
                  background: "transparent", border: "none", borderBottom: "1px solid var(--border-subtle)",
                  color: "var(--text-secondary)", fontSize: 12, cursor: "pointer",
                  transition: "background 0.1s",
                }}
                onMouseEnter={e => e.currentTarget.style.background = "var(--accent-subtle)"}
                onMouseLeave={e => e.currentTarget.style.background = "transparent"}
              >
                <div style={{ fontWeight: 600, color: "var(--text-primary)", marginBottom: 2 }}>{t.label}</div>
                <div style={{ fontSize: 11, color: "var(--text-tertiary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.prompt}</div>
              </button>
            ))}
          </div>
        )}

        {/* Template toggle + input row */}
        <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
          {templates && templates.length > 0 && (
            <button
              onClick={() => setShowTemplates(!showTemplates)}
              title="选择模板"
              style={{
                padding: "8px 10px", flexShrink: 0, background: showTemplates ? "var(--accent-subtle)" : "transparent",
                border: `1px solid ${showTemplates ? "var(--accent)" : "var(--border)"}`,
                borderRadius: "var(--radius-sm)", cursor: "pointer",
                color: showTemplates ? "var(--accent)" : "var(--text-tertiary)",
                fontSize: 14, lineHeight: 1, transition: "all 0.15s",
              }}
            >
              T
            </button>
          )}
          <textarea
            ref={inputRef}
            className="input"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder || "输入消息..."}
            rows={1}
            style={{
              flex: 1, minHeight: 36, maxHeight: 120, resize: "none",
              fontSize: 13, lineHeight: 1.5, padding: "8px 12px",
            }}
          />
          {isGenerating ? (
            <button className="btn" style={{ padding: "8px 14px", flexShrink: 0 }}
              onClick={onStopGeneration}>
              终止
            </button>
          ) : (
            <button className="btn-primary" style={{ padding: "8px 16px", flexShrink: 0 }}
              onClick={handleSend} disabled={!input.trim()}>
              发送
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

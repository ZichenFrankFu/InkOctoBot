import React, { useEffect, useState, useCallback } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import type { Project } from "../api/types";

export default function ProjectListPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formName, setFormName] = useState("");
  const [formGenre, setFormGenre] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiGet<{ items: Project[] }>("/api/data/projects");
      setProjects(r.items || []);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleCreate = async () => {
    if (!formName.trim()) return;
    await apiPost("/api/data/projects", { name: formName.trim(), genre: formGenre.trim() || undefined });
    setFormName("");
    setFormGenre("");
    setShowForm(false);
    load();
  };

  const handleUpdate = async () => {
    if (!editingId || !formName.trim()) return;
    await apiPut(`/api/data/projects/${editingId}`, { name: formName.trim(), genre: formGenre.trim() || undefined });
    setEditingId(null);
    setFormName("");
    setFormGenre("");
    load();
  };

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm("确定删除该项目？此操作不可撤销。")) return;
    await apiDelete(`/api/data/projects/${id}`);
    load();
  };

  const startEdit = (p: Project, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingId(p.id);
    setFormName(p.name);
    setFormGenre(p.genre || "");
    setShowForm(false);
  };

  const cancelForm = () => {
    setShowForm(false);
    setEditingId(null);
    setFormName("");
    setFormGenre("");
  };

  const handleExport = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    // Placeholder for export functionality
    alert("导出功能即将上线");
  };

  const formatDate = (dateStr?: string) => {
    if (!dateStr) return "--";
    const d = new Date(dateStr);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  };

  return (
    <div className="page-container">
      {/* Header */}
      <div className="page-header" style={{ padding: 0 }}>
        <div className="page-header-row">
          <div>
            <h2>项目列表</h2>
            <p>创建和管理你的网文创作项目</p>
          </div>
          <button className="btn-primary" onClick={() => { setShowForm(true); setEditingId(null); setFormName(""); setFormGenre(""); }}>
            + 新建项目
          </button>
        </div>
      </div>

      {/* Inline create/edit form */}
      {(showForm || editingId) && (
        <div className="card mt-24" style={{ animation: "slideUp 0.2s var(--ease-out)" }}>
          <div className="card-header">
            <h3>{editingId ? "编辑项目" : "新建项目"}</h3>
          </div>
          <div className="card-body">
            <div className="field-row" style={{ alignItems: "flex-end" }}>
              <div className="field" style={{ flex: 2 }}>
                <label className="label">项目名称</label>
                <input
                  className="input"
                  value={formName}
                  onChange={e => setFormName(e.target.value)}
                  placeholder="例：星辰大海"
                  autoFocus
                  onKeyDown={e => { if (e.key === "Enter") editingId ? handleUpdate() : handleCreate(); }}
                />
              </div>
              <div className="field" style={{ flex: 1 }}>
                <label className="label">题材 / 类型</label>
                <input
                  className="input"
                  value={formGenre}
                  onChange={e => setFormGenre(e.target.value)}
                  placeholder="例：玄幻、都市"
                  onKeyDown={e => { if (e.key === "Enter") editingId ? handleUpdate() : handleCreate(); }}
                />
              </div>
              <button className="btn-primary" onClick={editingId ? handleUpdate : handleCreate} disabled={!formName.trim()}>
                {editingId ? "保存" : "创建"}
              </button>
              <button className="btn" onClick={cancelForm}>取消</button>
            </div>
          </div>
        </div>
      )}

      {/* Content */}
      <div className="mt-24">
        {loading ? (
          <div className="loading">
            <div className="loading-spinner" />
            加载中...
          </div>
        ) : projects.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">&#x1F4C2;</div>
            <h4>还没有项目</h4>
            <p>点击「新建项目」开始你的创作之旅</p>
            {!showForm && (
              <button className="btn-primary mt-16" onClick={() => setShowForm(true)}>
                + 新建项目
              </button>
            )}
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 16 }}>
            {projects.map(p => (
              <div
                key={p.id}
                className="card"
                style={{ cursor: "pointer", transition: "border-color 0.15s, box-shadow 0.2s, transform 0.2s var(--ease-out)" }}
                onClick={() => {
                  // Navigate to project detail / editor
                  window.location.hash = `#/editor/${p.id}`;
                }}
                onMouseEnter={e => {
                  (e.currentTarget as HTMLElement).style.transform = "translateY(-2px)";
                  (e.currentTarget as HTMLElement).style.boxShadow = "var(--shadow-md)";
                }}
                onMouseLeave={e => {
                  (e.currentTarget as HTMLElement).style.transform = "";
                  (e.currentTarget as HTMLElement).style.boxShadow = "";
                }}
              >
                {/* Color bar */}
                <div style={{ height: 3, background: "var(--accent)" }} />
                <div className="card-body">
                  <div className="flex items-center justify-between mb-8">
                    <h3 className="font-serif" style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
                      {p.name}
                    </h3>
                    {p.genre && <span className="tag accent">{p.genre}</span>}
                  </div>

                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 14 }}>
                    <div>
                      <div className="text-xs text-muted">总字数</div>
                      <div className="font-mono" style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)" }}>
                        {(p.word_count || 0).toLocaleString()}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-muted">章节数</div>
                      <div className="font-mono" style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)" }}>
                        {p.chapter_count || 0}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted">
                      创建于 {formatDate(p.created_at)}
                    </span>
                    <div className="flex gap-4">
                      <button className="btn-icon" title="编辑" onClick={e => startEdit(p, e)}>
                        &#9998;
                      </button>
                      <button className="btn-icon" title="导出" onClick={e => handleExport(p.id, e)}>
                        &#8681;
                      </button>
                      <button
                        className="btn-icon"
                        title="删除"
                        onClick={e => handleDelete(p.id, e)}
                        style={{ color: "var(--error)" }}
                      >
                        &#10005;
                      </button>
                    </div>
                  </div>

                  {p.status && (
                    <div className="mt-8">
                      <span className={`tag ${p.status === "active" ? "status-ongoing" : "status-completed"}`}>
                        {p.status === "active" ? "进行中" : p.status === "completed" ? "已完结" : p.status}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

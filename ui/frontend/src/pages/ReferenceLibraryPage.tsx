import React from "react";

export default function ReferenceLibraryPage() {
  return (
    <div className="page-container">
      <div className="page-header">
        <h2>参考作品库</h2>
        <p>管理和浏览已分析的参考作品 — 提取风格指纹、叙事结构、角色画像</p>
      </div>

      <div className="placeholder-page">
        <div className="placeholder-hero">
          <span className="placeholder-hero-icon">📚</span>
          <h3>参考作品库</h3>
          <p>系统的"学习素材库"，存储经过深度分析的完整作品特征</p>
        </div>

        <div className="placeholder-features">
          <div className="placeholder-feature-card">
            <div className="placeholder-feature-icon">🔍</div>
            <h4>浏览与筛选</h4>
            <p>按 genre / 评分 / 来源筛选所有已分析作品，查看风格雷达图、叙事结构图、角色关系网络</p>
          </div>
          <div className="placeholder-feature-card">
            <div className="placeholder-feature-icon">📤</div>
            <h4>上传作品</h4>
            <p>上传完整 TXT 文件，经预处理 Pipeline 自动提取风格指纹、叙事结构、角色画像</p>
          </div>
          <div className="placeholder-feature-card">
            <div className="placeholder-feature-icon">📥</div>
            <h4>从榜单导入</h4>
            <p>批量将爬虫采集的热门作品导入参考库，自动特征提取</p>
          </div>
          <div className="placeholder-feature-card">
            <div className="placeholder-feature-icon">🔗</div>
            <h4>关联到项目</h4>
            <p>在创建项目时从库中选择参考作品，关联到世界观、角色、风格等具体维度</p>
          </div>
        </div>

        <div className="placeholder-db-preview">
          <h4>参考作品数据结构预览</h4>
          <div className="placeholder-schema">
            <div className="schema-row"><span className="schema-field">ref_id</span><span className="schema-type">TEXT PK</span></div>
            <div className="schema-row"><span className="schema-field">title / author</span><span className="schema-type">TEXT</span></div>
            <div className="schema-row"><span className="schema-field">source</span><span className="schema-type">'platform' | 'upload'</span></div>
            <div className="schema-row"><span className="schema-field">style_fingerprint_json</span><span className="schema-type">JSON — 风格指纹</span></div>
            <div className="schema-row"><span className="schema-field">narrative_structure_json</span><span className="schema-type">JSON — 叙事结构</span></div>
            <div className="schema-row"><span className="schema-field">extracted_characters_json</span><span className="schema-type">JSON — 角色画像</span></div>
            <div className="schema-row"><span className="schema-field">rhythm_template_json</span><span className="schema-type">JSON — 节奏模板</span></div>
          </div>
        </div>
      </div>
    </div>
  );
}

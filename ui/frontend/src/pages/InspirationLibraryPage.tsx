/**
 * InspirationLibraryPage — promoted from a tab inside
 * ReferenceSearchPage to a top-level page under the new
 * "灵感数据库" nav group.
 *
 * Thin wrapper: delegates to the existing <InspirationLibrary>
 * component. The "在参考作品中搜索" callback now routes the user
 * to the dedicated inspiration search page via onNavigate.
 */
import React from "react";
import InspirationLibrary from "../components/InspirationLibrary";


interface Props {
  onNavigate?: (tab: string, ctx?: any) => void;
}


export default function InspirationLibraryPage({ onNavigate }: Props) {
  return (
    <div className="page" style={{ padding: 16 }}>
      <header style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>💡 灵感库</h2>
        <p style={{ color: "var(--text-tertiary)", fontSize: 12, margin: "4px 0 0" }}>
          自由记录的创作灵感片段：场景、情节装置、人物设计等。带 embedding，
          可在生成时按章节内容相关性自动召回。
        </p>
      </header>
      <InspirationLibrary
        onSearchWorks={(text) => {
          // Persist the query to sessionStorage so the search page
          // can pick it up on mount.
          try {
            sessionStorage.setItem("inspiration_search_query", text);
          } catch {/* ignore */}
          onNavigate?.("inspiration-search", { query: text });
        }}
      />
    </div>
  );
}

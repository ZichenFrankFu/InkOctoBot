/**
 * InspirationSearchPage — promoted from a tab inside
 * ReferenceSearchPage to a top-level page under the new
 * "灵感数据库" nav group.
 *
 * Thin wrapper that mounts ReferenceSearchPage with the search tab
 * pre-selected and the tab bar hidden — same underlying logic, just
 * a focused entry point.
 */
import React from "react";
import ReferenceSearchPage from "./ReferenceSearchPage";


interface Props {
  onNavigate?: (tab: string) => void;
}


export default function InspirationSearchPage({ onNavigate }: Props) {
  return (
    <ReferenceSearchPage
      onNavigate={onNavigate}
      initialTab="search"
      hideTabs={true}
      pageTitle=" 灵感搜索（跨参考作品）"
      pageSubtitle="用自然语言在已索引的参考作品中找到相关段落、人物、设定"
    />
  );
}

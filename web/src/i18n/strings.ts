import type { Lang } from "./pick";

// Every user-visible string outside article content. `Dict` is derived from `en`, so a key missing from `zh` is a compile error.
const en = {
  brand: "AI News Brief",
  navMain: "Main",
  navDigest: "Digest",
  navBrowse: "Browse",
  footer: "Local read-only view of your daily digest",
  settings: "Settings",
  language: "Language",
  theme: "Theme",
  themeSystem: "System",
  themeLight: "Light",
  themeDark: "Dark",

  untitled: "Untitled",
  rank: (n: number) => `Rank ${n}`,
  original: "Original",
  importance: (n: number) => `Importance ${n}`,
  tags: "Tags",

  category: "Category",
  categoryAll: "All",
  categories: {
    news: "news",
    discussion: "discussion",
    video: "video",
    opensource: "opensource",
  } as Record<string, string>,

  date: "Date",
  search: "Search",
  previous: "Previous",
  next: "Next",
  pageOf: (page: number, pages: number) => `Page ${page} of ${pages}`,
  pagination: "Pagination",
  showFewerTags: "Show fewer",
  showAllTags: (n: number) => `Show all ${n} tags`,
  retry: "Retry",

  dailyDigest: "Daily digest",
  loadingDigests: "Loading digests…",
  loadingDigest: "Loading digest…",
  noDigests: "No digests yet. Run the pipeline to create one.",
  noArticlesForDate: "No articles for this date.",
  couldNotLoadDigests: (m: string) => `Could not load digests: ${m}`,
  couldNotLoadDigest: (m: string) => `Could not load digest: ${m}`,

  browseArticles: "Browse articles",
  loadingArticles: "Loading articles…",
  noArticlesMatch: "No articles match these filters.",
  couldNotLoadArticles: (m: string) => `Could not load articles: ${m}`,

  backToArticles: "← Back to articles",
  loadingArticle: "Loading article…",
  articleNotFound: "Article not found.",
  couldNotLoadArticle: (m: string) => `Could not load article: ${m}`,
  openOriginal: "Open original",

  pageNotFound: "Page not found",
  backToDigest: "Back to digest",
};

export type Dict = typeof en;

const zh: Dict = {
  brand: "AI 新闻简报",
  navMain: "主导航",
  navDigest: "每日简报",
  navBrowse: "浏览",
  footer: "每日简报的本地只读视图",
  settings: "设置",
  language: "语言",
  theme: "主题",
  themeSystem: "跟随系统",
  themeLight: "浅色",
  themeDark: "深色",

  untitled: "无标题",
  rank: (n) => `第 ${n} 名`,
  original: "原文",
  importance: (n) => `重要度 ${n}`,
  tags: "标签",

  category: "分类",
  categoryAll: "全部",
  categories: {
    news: "新闻",
    discussion: "讨论",
    video: "视频",
    opensource: "开源",
  },

  date: "日期",
  search: "搜索",
  previous: "上一页",
  next: "下一页",
  pageOf: (page, pages) => `第 ${page} / ${pages} 页`,
  pagination: "分页",
  showFewerTags: "收起",
  showAllTags: (n) => `显示全部 ${n} 个标签`,
  retry: "重试",

  dailyDigest: "每日简报",
  loadingDigests: "正在加载简报…",
  loadingDigest: "正在加载简报…",
  noDigests: "暂无简报。请运行流水线生成。",
  noArticlesForDate: "这一天没有文章。",
  couldNotLoadDigests: (m) => `无法加载简报列表：${m}`,
  couldNotLoadDigest: (m) => `无法加载简报：${m}`,

  browseArticles: "浏览文章",
  loadingArticles: "正在加载文章…",
  noArticlesMatch: "没有符合筛选条件的文章。",
  couldNotLoadArticles: (m) => `无法加载文章：${m}`,

  backToArticles: "← 返回文章列表",
  loadingArticle: "正在加载文章…",
  articleNotFound: "未找到该文章。",
  couldNotLoadArticle: (m) => `无法加载文章：${m}`,
  openOriginal: "打开原文",

  pageNotFound: "页面不存在",
  backToDigest: "返回每日简报",
};

export const STRINGS: Record<Lang, Dict> = { en, zh };

// Category comes from the API as a free string; plain indexing would hit Object.prototype ("constructor", "toString").
export function categoryLabel(t: Dict, category: string): string {
  return Object.hasOwn(t.categories, category) ? t.categories[category] : category;
}

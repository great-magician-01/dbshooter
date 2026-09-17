/**
 * 全局内联 SVG 图标路径(16×16 viewBox,stroke=currentColor)。
 * 集中维护,用于替换原先的 emoji / Unicode 字形图标。
 * 均为静态可信常量,可安全用于 v-html。
 */
export const ICON_PATHS: Record<string, string> = {
  // 树节点展开箭头(实心三角,随 .tn.open 旋转)
  caret: '<path d="M6.2 4.2l4.6 3.8-4.6 3.8z" fill="currentColor" stroke="none"/>',
  // 元数据类型
  table: '<path d="M2.5 3.5h11v9h-11z"/><path d="M2.5 6.5h11M6.2 6.5v6"/>',
  view: '<path d="M1.8 8c1.4-2.8 3.6-4.2 6.2-4.2s4.8 1.4 6.2 4.2c-1.4 2.8-3.6 4.2-6.2 4.2S3.2 10.8 1.8 8z"/><circle cx="8" cy="8" r="1.9"/>',
  column: '<path d="M4.5 8h7"/>',
  key: '<circle cx="5.2" cy="5.2" r="2.6"/><path d="M7.1 7.1L13 13M10.7 9.7l1.8-1.8M12.3 11.3l1.6-1.6"/>',
  pk: '<circle cx="5.2" cy="5.2" r="2.6"/><path d="M7.1 7.1L13 13M10.7 9.7l1.8-1.8M12.3 11.3l1.6-1.6"/>',
  keygroup: '<path d="M2 4.5h4.2l1.6 1.8H14v7.2H2z"/>',
  collection: '<path d="M8 2.6l5.6 2.6L8 7.8 2.4 5.2z"/><path d="M2.4 8.2L8 10.8l5.6-2.6M2.4 11L8 13.6l5.6-2.6"/>',
  index: '<path d="M2.5 8h8.5M8 4.8L11.2 8 8 11.2M13.5 3.8v8.4"/>',
  database: '<ellipse cx="8" cy="4.2" rx="5" ry="2"/><path d="M3 4.2v7.6c0 1.1 2.2 2 5 2s5-.9 5-2V4.2M3 8c0 1.1 2.2 2 5 2s5-.9 5-2"/>',
  dot: '<circle cx="8" cy="8" r="1.6" fill="currentColor" stroke="none"/>',
  // 树节点加载失败提示(见 TreeNode.vue 的 errorNode)
  warn: '<path d="M8 2.8l6.2 10.4H1.8z"/><path d="M8 6.6v3.2M8 11.7v.1"/>',
  // 页签 / 面板
  sql: '<path d="M5.6 4.6L2.6 8l3 3.4M10.4 4.6L13.4 8l-3 3.4"/>',
  redis: '<circle cx="5.2" cy="5.2" r="2.6"/><path d="M7.1 7.1L13 13M10.7 9.7l1.8-1.8M12.3 11.3l1.6-1.6"/>',
  mongo: '<path d="M8 2.6l5.6 2.6L8 7.8 2.4 5.2z"/><path d="M2.4 8.2L8 10.8l5.6-2.6M2.4 11L8 13.6l5.6-2.6"/>',
  // 操作
  close: '<path d="M4.2 4.2l7.6 7.6M11.8 4.2l-7.6 7.6"/>',
  edit: '<path d="M9.8 3l3.2 3.2-7.4 7.4H2.4v-3.2z"/><path d="M8.4 4.4l3.2 3.2"/>',
  check: '<path d="M3 8.6l3.4 3.4L13 4.6"/>',
  plus: '<path d="M8 3.2v9.6M3.2 8h9.6"/>',
  play: '<path d="M5 3.4l7 4.6-7 4.6z" fill="currentColor" stroke="none"/>',
  // 主题 / 设置
  sun: '<circle cx="8" cy="8" r="3"/><path d="M8 1.6v1.8M8 12.6v1.8M1.6 8h1.8M12.6 8h1.8M3.5 3.5l1.3 1.3M11.2 11.2l1.3 1.3M12.5 3.5l-1.3 1.3M4.8 11.2l-1.3 1.3"/>',
  moon: '<path d="M13.4 9.9A6 6 0 0 1 6.1 2.6a6 6 0 1 0 7.3 7.3z"/>',
  gear: '<circle cx="8" cy="8" r="2.2"/><path d="M8 3v2.4M8 10.6V13M3 8h2.4M10.6 8H13M4.5 4.5l1.7 1.7M9.8 9.8l1.7 1.7M11.5 4.5L9.8 6.2M6.2 9.8l-1.7 1.7"/>',
  chevron: '<path d="M4 6.2l4 4 4-4"/>',
}

/** 生成完整 SVG 字符串(用于日志等 v-html 场景) */
export function iconSvg(name: string, size = 10): string {
  return `<svg width="${size}" height="${size}" viewBox="0 0 16 16" fill="none" `
    + `stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" `
    + `aria-hidden="true" style="vertical-align:-1.5px">${ICON_PATHS[name] ?? ICON_PATHS.dot}</svg>`
}

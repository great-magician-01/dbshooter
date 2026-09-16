/** 滚动相关的纯判定逻辑。 */

/**
 * 滚动容器是否已接近底部,用于无限滚动自动加载。
 * threshold 为距底部的像素阈值;内容不足一屏(无滚动条)时恒为 true,
 * 便于结果渲染后补拉一页填满可视区。
 */
export function nearBottom(
  el: Pick<HTMLElement, 'scrollTop' | 'clientHeight' | 'scrollHeight'>,
  threshold = 160,
): boolean {
  return el.scrollTop + el.clientHeight >= el.scrollHeight - threshold
}

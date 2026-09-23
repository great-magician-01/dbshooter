/**
 * ER 图布局(纯函数,零 Vue/DOM 依赖,可单测)。
 *
 * 单表中心布局:被查询表居中;它引用的表(出站 FK)排右侧,
 * 引用它的表(入站 FK)排左侧;边一律 FK 侧 → 被引侧(左 → 右)。
 * 坐标为确定性布局(同输入同输出),不感知缩放(组件层用 <g transform> 缩放)。
 */
import type { TableRelation } from '@/types'

// ── 几何常量 ──
export const NODE_W = 220        // 节点卡片宽
export const HEADER_H = 28       // 卡片表头(表名)高
export const ROW_H = 22          // 单列行高
const GAP_X = 90                 // 左右列之间的水平间距(走线空间)
const GAP_Y = 24                 // 同列栈内节点垂直间距
export const PAD = 24            // 画布内边距
const MAX_SIDE = 20              // 每侧邻居上限,超出计入 overflow
const SELF_LOOP_OUT = 28         // 自引用回环向右探出距离
const PARALLEL_SPREAD = 12       // 锚点重合的平行边垂直错开步长

/** 节点上的一列(只需名称与主键标记) */
export interface ErCol { name: string; pk: boolean }

/** 布局输入:一张表(中心或邻居) */
export interface ErNode {
  key: string                    // 唯一键:pg/mysql `${schema}.${table}`,sqlite 表名
  label: string                  // 展示名(同 schema 省略前缀)
  side: 'left' | 'center' | 'right'
  columns: ErCol[]
}

/** 布局输入:一条外键边(复合 FK 已塌缩为单列对) */
export interface ErEdge {
  name: string                   // 约束名
  fromTable: string              // FK 侧节点 key
  fromColumn: string
  toTable: string                // 被引侧节点 key
  toColumn: string
}

export interface ErNodeBox extends ErNode { x: number; y: number; w: number; h: number }

/** 一条可渲染的边:SVG path + 标签锚点 */
export interface ErEdgePath { name: string; d: string; lx: number; ly: number }

export interface ErLayout {
  nodes: ErNodeBox[]
  edges: ErEdgePath[]
  width: number
  height: number
}

/** 节点内某列行的锚点 y(行中心);列名找不到时回退到首行中心,不丢边 */
function colY(node: ErNodeBox, column: string): number {
  const idx = node.columns.findIndex(c => c.name === column)
  return node.y + HEADER_H + ROW_H * ((idx < 0 ? 0 : idx) + 0.5)
}

/** 边端点 x:取朝向对方的一侧边沿 */
function edgeX(from: ErNodeBox, to: ErNodeBox): [number, number] {
  return from.x < to.x ? [from.x + from.w, to.x] : [from.x, to.x + to.w]
}

/** 布局:三列栈(左 = 引用我的,中 = 当前表,右 = 我引用的) */
export function layoutEr(nodes: ErNode[], edges: ErEdge[]): ErLayout {
  const left = nodes.filter(n => n.side === 'left').sort((a, b) => a.label.localeCompare(b.label))
  const center = nodes.filter(n => n.side === 'center')
  const right = nodes.filter(n => n.side === 'right').sort((a, b) => a.label.localeCompare(b.label))

  const leftX = PAD
  const centerX = PAD + (left.length ? NODE_W + GAP_X : 0)
  const rightX = centerX + NODE_W + (right.length ? GAP_X : 0)

  const boxes: ErNodeBox[] = []
  const stack = (list: ErNode[], x: number): number => {
    let y = PAD
    for (const n of list) {
      const h = HEADER_H + ROW_H * Math.max(1, n.columns.length)
      boxes.push({ ...n, x, y, w: NODE_W, h })
      y += h + GAP_Y
    }
    return y - GAP_Y  // 栈底(空栈返回 PAD - GAP_Y,下方统一取 max 兜底)
  }
  const bottom = Math.max(stack(left, leftX), stack(center, centerX),
                          stack(right, rightX), PAD)

  const byKey = new Map(boxes.map(b => [b.key, b]))

  // 原始锚点
  interface RawEdge extends ErEdge { x1: number; y1: number; x2: number; y2: number }
  const raws: RawEdge[] = []
  for (const e of edges) {
    const from = byKey.get(e.fromTable)
    const to = byKey.get(e.toTable)
    if (!from || !to) continue              // 端点不在图里(如超 cap 被裁)跳过
    const y1 = colY(from, e.fromColumn)
    const y2 = colY(to, e.toColumn)
    const [x1, x2] = from === to ? [from.x + from.w, from.x + from.w] : edgeX(from, to)
    raws.push({ ...e, x1, y1, x2, y2 })
  }

  // 平行边(同两端 + 同锚点):垂直错开并 clamp 到节点体内;锚点不同的天然分开,不动
  const groups = new Map<string, RawEdge[]>()
  for (const r of raws) {
    const gk = `${r.fromTable}${r.toTable}${r.y1}${r.y2}`
    const list = groups.get(gk)
    if (list) list.push(r)
    else groups.set(gk, [r])
  }
  const clampBody = (node: ErNodeBox, y: number): number =>
    Math.min(Math.max(y, node.y + HEADER_H + ROW_H / 2), node.y + node.h - ROW_H / 2)
  for (const list of groups.values()) {
    if (list.length < 2) continue
    list.forEach((r, i) => {
      const off = (i - (list.length - 1) / 2) * PARALLEL_SPREAD
      const from = byKey.get(r.fromTable)!
      const to = byKey.get(r.toTable)!
      r.y1 = clampBody(from, r.y1 + off)
      r.y2 = clampBody(to, r.y2 + off)
    })
  }

  const paths: ErEdgePath[] = raws.map(r => {
    if (r.fromTable === r.toTable) {
      // 自引用:右侧回环折线(出 → 探出 → 竖直 → 回来)
      const x = r.x1 + SELF_LOOP_OUT
      return { name: r.name, d: `M ${r.x1} ${r.y1} H ${x} V ${r.y2} H ${r.x1}`,
               lx: x + 4, ly: (r.y1 + r.y2) / 2 - 4 }
    }
    const bend = Math.max(24, Math.abs(r.x2 - r.x1) * 0.5)
    return { name: r.name,
             d: `M ${r.x1} ${r.y1} C ${r.x1 + bend} ${r.y1}, ${r.x2 - bend} ${r.y2}, ${r.x2} ${r.y2}`,
             lx: (r.x1 + r.x2) / 2, ly: (r.y1 + r.y2) / 2 - 4 }
  })

  return {
    nodes: boxes,
    edges: paths,
    width: (right.length ? rightX + NODE_W : centerX + NODE_W) + PAD,
    height: bottom + PAD,
  }
}

/** 节点 key 规则:sqlite 的 schema 恒 'main' 时省略,其余 `${schema}.${table}` */
export const defaultKeyOf = (schema: string, table: string): string =>
  schema === 'main' || !schema ? table : `${schema}.${table}`

export interface ErGraph {
  nodes: ErNode[]
  edges: ErEdge[]
  /** 每侧被 cap 裁掉的邻居数(UI 提示"另有 N 张关联表未展示") */
  overflow: { left: number; right: number }
}

/**
 * 由后端 relations 构建布局输入:
 * - 复合 FK(同 direction|name|table|ref_table 多行)塌缩成一条边,取 seq 最小列对;
 * - 自引用不加邻居节点,边两端都是中心(布局走回环分支);
 * - columnsOf 取邻居列(未取到则空列卡片,只画表头)。
 */
export function buildGraph(
  centerKey: string,
  centerLabel: string,
  centerCols: ErCol[],
  relations: TableRelation[],
  columnsOf: (key: string) => ErCol[] | undefined,
  keyOf: (schema: string, table: string) => string = defaultKeyOf,
): ErGraph {
  // 中心表的 schema(推断邻居标签是否带前缀):出站取 FK 侧,入站取被引侧
  const first = relations[0]
  const centerSchema = first ? (first.direction === 'out' ? first.schema : first.ref_schema) : ''

  // 复合 FK 塌缩:同约束多行取 seq 最小的列对
  const collapsed = new Map<string, TableRelation>()
  for (const r of relations) {
    const gk = `${r.direction}|${r.name}|${r.table}|${r.ref_table}`
    const prev = collapsed.get(gk)
    if (!prev || r.seq < prev.seq) collapsed.set(gk, r)
  }

  const nodes = new Map<string, ErNode>()
  const edges: ErEdge[] = []
  for (const r of collapsed.values()) {
    if (r.direction === 'out') {
      const nKey = keyOf(r.ref_schema, r.ref_table)
      edges.push({ name: r.name, fromTable: centerKey, fromColumn: r.column,
                   toTable: nKey, toColumn: r.ref_column })
      if (nKey !== centerKey && !nodes.has(nKey)) {
        nodes.set(nKey, { key: nKey, side: 'right',
          label: r.ref_schema === centerSchema ? r.ref_table : `${r.ref_schema}.${r.ref_table}`,
          columns: columnsOf(nKey) ?? [] })
      }
    } else {
      const nKey = keyOf(r.schema, r.table)
      // 自引用后端只记 'out';防御:'in' 指回中心时同样不加节点
      edges.push({ name: r.name, fromTable: nKey, fromColumn: r.column,
                   toTable: centerKey, toColumn: r.ref_column })
      if (nKey !== centerKey && !nodes.has(nKey)) {
        nodes.set(nKey, { key: nKey, side: 'left',
          label: r.schema === centerSchema ? r.table : `${r.schema}.${r.table}`,
          columns: columnsOf(nKey) ?? [] })
      }
    }
  }

  // 每侧 cap:按 label 排序后截断(与 layoutEr 的排序口径一致,裁掉的就是排尾部的)
  const cap = (side: 'left' | 'right'): number => {
    const list = [...nodes.values()].filter(n => n.side === side)
      .sort((a, b) => a.label.localeCompare(b.label))
    const extra = list.length - MAX_SIDE
    if (extra <= 0) return 0
    const dropped = new Set(list.slice(MAX_SIDE).map(n => n.key))
    for (const k of dropped) nodes.delete(k)
    // 被裁节点的边一并丢弃(layoutEr 也会按缺端点跳过,这里保持数据干净)
    for (let i = edges.length - 1; i >= 0; i--) {
      if (dropped.has(edges[i].fromTable) || dropped.has(edges[i].toTable)) edges.splice(i, 1)
    }
    return extra
  }
  const overflow = { left: cap('left'), right: cap('right') }

  return {
    nodes: [{ key: centerKey, label: centerLabel, side: 'center', columns: centerCols },
            ...nodes.values()],
    edges,
    overflow,
  }
}

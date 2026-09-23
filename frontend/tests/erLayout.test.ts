/** utils/erLayout 纯函数单测:布局坐标、锚点、回环、平行边、图构建。 */
import { describe, expect, it } from 'vitest'

import type { TableRelation } from '@/types'
import { buildGraph, HEADER_H, layoutEr, NODE_W, PAD, ROW_H } from '@/utils/erLayout'
import type { ErEdge, ErNode } from '@/utils/erLayout'

const rel = (over: Partial<TableRelation>): TableRelation => ({
  name: 'fk1', direction: 'out', schema: 'main', table: 'orders', column: 'user_id',
  ref_schema: 'main', ref_table: 'users', ref_column: 'id', seq: 0, ...over,
})

describe('layoutEr 三列布局', () => {
  const nodes: ErNode[] = [
    { key: 'orders', label: 'orders', side: 'center',
      columns: [{ name: 'id', pk: true }, { name: 'user_id', pk: false }] },
    { key: 'customers', label: 'customers', side: 'left',
      columns: [{ name: 'oid', pk: false }] },
    { key: 'users', label: 'users', side: 'right',
      columns: [{ name: 'id', pk: true }, { name: 'name', pk: false }] },
  ]

  it('坐标:左 PAD、中 PAD+宽+间距、右再递进;高 = 表头 + 行 × 行高', () => {
    const layout = layoutEr(nodes, [])
    const byKey = new Map(layout.nodes.map(n => [n.key, n]))
    expect(byKey.get('customers')!.x).toBe(PAD)
    expect(byKey.get('orders')!.x).toBe(PAD + NODE_W + 90)
    expect(byKey.get('users')!.x).toBe(PAD + NODE_W + 90 + NODE_W + 90)
    expect(byKey.get('orders')!.y).toBe(PAD)
    expect(byKey.get('orders')!.h).toBe(HEADER_H + ROW_H * 2)
    // 画布尺寸:右列右缘 + PAD;无右侧列时以中列为准
    expect(layout.width).toBe(PAD + NODE_W + 90 + NODE_W + 90 + NODE_W + PAD)
    expect(layoutEr([nodes[0]], []).width).toBe(PAD + NODE_W + PAD)
  })

  it('边锚点:列行中心;朝向对方一侧出边;贝塞尔路径', () => {
    const edges: ErEdge[] = [
      { name: 'fk1', fromTable: 'orders', fromColumn: 'user_id', toTable: 'users', toColumn: 'id' },
      { name: 'fk2', fromTable: 'customers', fromColumn: 'oid', toTable: 'orders', toColumn: 'id' },
    ]
    const layout = layoutEr(nodes, edges)
    expect(layout.edges).toHaveLength(2)
    const e1 = layout.edges[0]
    // orders.user_id 行中心 y = PAD + HEADER_H + 1.5*ROW_H;users.id 为 0.5*ROW_H
    const y1 = PAD + HEADER_H + ROW_H * 1.5
    const y2 = PAD + HEADER_H + ROW_H * 0.5
    const x1 = PAD + NODE_W + 90 + NODE_W          // orders 右缘
    const x2 = PAD + NODE_W + 90 + NODE_W + 90     // users 左缘
    expect(e1.d).toBe(`M ${x1} ${y1} C ${x1 + 45} ${y1}, ${x2 - 45} ${y2}, ${x2} ${y2}`)
    expect(e1.lx).toBe((x1 + x2) / 2)
  })

  it('列名缺失回退首行中心;未知端点的边被跳过', () => {
    const layout = layoutEr(nodes, [
      { name: 'fk9', fromTable: 'orders', fromColumn: 'nope', toTable: 'users', toColumn: 'nope' },
      { name: 'fkX', fromTable: 'ghost', fromColumn: 'a', toTable: 'users', toColumn: 'id' },
    ])
    expect(layout.edges).toHaveLength(1)
    const y1 = PAD + HEADER_H + ROW_H * 0.5
    expect(layout.edges[0].d).toContain(` ${y1}`)
  })

  it('自引用:右侧回环折线', () => {
    const layout = layoutEr(
      [{ key: 'employees', label: 'employees', side: 'center',
         columns: [{ name: 'id', pk: true }, { name: 'manager_id', pk: false }] }],
      [{ name: 'fk_emp', fromTable: 'employees', fromColumn: 'manager_id',
         toTable: 'employees', toColumn: 'id' }])
    const x = PAD + NODE_W
    const y1 = PAD + HEADER_H + ROW_H * 1.5
    const y2 = PAD + HEADER_H + ROW_H * 0.5
    expect(layout.edges[0].d).toBe(`M ${x} ${y1} H ${x + 28} V ${y2} H ${x}`)
  })

  it('平行边(同端点同锚点):垂直错开,越界 clamp 在节点体内', () => {
    const two: ErEdge[] = [1, 2].map(i => ({
      name: `fk${i}`, fromTable: 'orders', fromColumn: 'user_id',
      toTable: 'users', toColumn: 'id',
    }))
    const layout = layoutEr(nodes, two)
    // user_id 是 orders 末行(锚点 85):+6 越界 clamp 回 85,-6 → 79;两条边仍可区分
    expect(layout.edges[0].d).toContain(' 79')
    expect(layout.edges[1].d).toContain(' 85')
    expect(layout.edges[0].d).not.toBe(layout.edges[1].d)
  })

  it('平行边锚点在中间行:不触发 clamp,精确 ±6', () => {
    const wide: ErNode[] = [
      { key: 'a', label: 'a', side: 'center',
        columns: [{ name: 'id', pk: true }, { name: 'f', pk: false }, { name: 'g', pk: false }] },
      { key: 'b', label: 'b', side: 'right',
        columns: [{ name: 'id', pk: true }, { name: 'x', pk: false }, { name: 'y', pk: false }] },
    ]
    const two: ErEdge[] = [1, 2].map(i => ({
      name: `fk${i}`, fromTable: 'a', fromColumn: 'f', toTable: 'b', toColumn: 'x',
    }))
    const layout = layoutEr(wide, two)
    const base = PAD + HEADER_H + ROW_H * 1.5   // f、x 都是第 2 行
    expect(layout.edges[0].d).toContain(` ${base - 6}`)
    expect(layout.edges[1].d).toContain(` ${base + 6}`)
  })

  it('平行边在单列节点(clamp 区间退化为一点):靠控制点差异区分', () => {
    const single: ErNode[] = [
      { key: 'a', label: 'a', side: 'center', columns: [{ name: 'f', pk: true }] },
      { key: 'b', label: 'b', side: 'right', columns: [{ name: 'x', pk: true }] },
    ]
    const two: ErEdge[] = [1, 2].map(i => ({
      name: `fk${i}`, fromTable: 'a', fromColumn: 'f', toTable: 'b', toColumn: 'x',
    }))
    const layout = layoutEr(single, two)
    // 垂直偏移无处可去,但两条曲线不能再逐字节相同
    expect(layout.edges[0].d).not.toBe(layout.edges[1].d)
    // 标签也不再重叠
    expect(layout.edges[0].ly).not.toBe(layout.edges[1].ly)
  })

  it('自引用:回环与标签计入画布宽,不被 SVG 视口裁掉', () => {
    const layout = layoutEr(
      [{ key: 'employees', label: 'employees', side: 'center',
         columns: [{ name: 'id', pk: true }, { name: 'manager_id', pk: false }] }],
      [{ name: 'fk_employees_manager', fromTable: 'employees', fromColumn: 'manager_id',
         toTable: 'employees', toColumn: 'id' }])
    expect(layout.edges[0].lx).toBeLessThanOrEqual(layout.width)
    // 无回环时画布宽度不变
    expect(layoutEr([{ key: 't', label: 't', side: 'center',
                      columns: [{ name: 'id', pk: true }] }], []).width)
      .toBe(PAD + NODE_W + PAD)
  })

  it('互引(A↔B):控制点朝向来侧,标签错开不重叠', () => {
    const nodes: ErNode[] = [
      { key: 'orders', label: 'orders', side: 'center',
        columns: [{ name: 'id', pk: true }, { name: 'customer_id', pk: false }] },
      { key: 'customers', label: 'customers', side: 'right',
        columns: [{ name: 'id', pk: true }, { name: 'last_order_id', pk: false }] },
    ]
    const edges: ErEdge[] = [
      { name: 'fk1', fromTable: 'orders', fromColumn: 'customer_id',
        toTable: 'customers', toColumn: 'id' },
      { name: 'fk2', fromTable: 'customers', fromColumn: 'last_order_id',
        toTable: 'orders', toColumn: 'id' },
    ]
    const layout = layoutEr(nodes, edges)
    const cx = PAD + NODE_W            // orders 右缘
    const rx = cx + 90                 // customers 左缘
    // 正向边:起点在左,第一个控制点向右(朝目标)
    expect(layout.edges[0].d).toContain(`M ${cx} 85 C ${cx + 45} 85`)
    // 反向边:起点在右,控制点必须也朝右(朝目标方向),
    // 固定 x1+bend/x2-bend 的老写法会把它甩到终点左侧成反 S 形
    expect(layout.edges[1].d).toContain(`M ${rx} 85 C ${rx - 45} 85`)
    expect(layout.edges[1].d).toContain(`${cx + 45} 63, ${cx} 63`)
    // 两条边锚点互换 → 标签锚点天然重合,必须错开
    expect(layout.edges[0].ly).not.toBe(layout.edges[1].ly)
  })

  it('左/右列内按 label 排序(确定性)', () => {
    const layout = layoutEr([
      { key: 'c', label: 'c', side: 'center', columns: [] },
      { key: 'z', label: 'zeta', side: 'right', columns: [] },
      { key: 'a', label: 'alpha', side: 'right', columns: [] },
    ], [])
    const rights = layout.nodes.filter(n => n.side === 'right')
    expect(rights.map(n => n.key)).toEqual(['a', 'z'])
    expect(rights[0].y).toBe(PAD)
    expect(rights[1].y).toBeGreaterThan(rights[0].y)
  })
})

describe('buildGraph 图构建', () => {
  const centerCols = [{ name: 'id', pk: true }, { name: 'user_id', pk: false }]
  const colsOf = () => [{ name: 'id', pk: true }]

  it('出站 → 右侧邻居,入站 → 左侧邻居;同 schema 标签省略前缀', () => {
    const g = buildGraph('orders', 'orders', centerCols, [
      rel({}),
      rel({ name: 'fk_in', direction: 'in', schema: 'main', table: 'logs',
            column: 'oid', ref_table: 'orders', ref_column: 'id' }),
    ], colsOf)
    const sides = new Map(g.nodes.map(n => [n.key, n.side]))
    expect(sides.get('orders')).toBe('center')
    expect(sides.get('users')).toBe('right')
    expect(sides.get('logs')).toBe('left')
    expect(g.nodes.find(n => n.key === 'users')!.label).toBe('users')
    // 边方向:一律 FK 侧 → 被引侧
    const eIn = g.edges.find(e => e.name === 'fk_in')!
    expect([eIn.fromTable, eIn.toTable]).toEqual(['logs', 'orders'])
  })

  it('复合 FK 塌缩为一条边,取 seq 最小列对', () => {
    const g = buildGraph('children', 'children', [], [
      rel({ name: 'fk_c', table: 'children', column: 'x', ref_table: 'parents',
            ref_column: 'a', seq: 0 }),
      rel({ name: 'fk_c', table: 'children', column: 'y', ref_table: 'parents',
            ref_column: 'b', seq: 1 }),
    ], colsOf)
    expect(g.edges).toHaveLength(1)
    expect(g.edges[0].fromColumn).toBe('x')
    expect(g.edges[0].toColumn).toBe('a')
    // 邻居只出现一次
    expect(g.nodes.filter(n => n.key === 'parents')).toHaveLength(1)
  })

  it('自引用不加邻居节点,边两端都是中心', () => {
    const g = buildGraph('employees', 'employees', [], [
      rel({ table: 'employees', column: 'manager_id', ref_table: 'employees',
            ref_column: 'id' }),
    ], colsOf)
    expect(g.nodes).toHaveLength(1)
    expect(g.edges[0].fromTable).toBe('employees')
    expect(g.edges[0].toTable).toBe('employees')
  })

  it('跨 schema 邻居:标签带 schema 前缀;keyOf 可定制', () => {
    const g = buildGraph('public.users', 'users', centerCols, [
      rel({ direction: 'in', schema: 'sales', table: 't1', column: 'uid',
            ref_schema: 'public', ref_table: 'users', ref_column: 'id' }),
    ], colsOf)
    const node = g.nodes.find(n => n.key === 'sales.t1')!
    expect(node.side).toBe('left')
    expect(node.label).toBe('sales.t1')
  })

  it('每侧 cap 20:超出计入 overflow 并裁掉节点与边', () => {
    const many: TableRelation[] = Array.from({ length: 22 }, (_, i) => rel({
      name: `fk${i}`, direction: 'in', schema: 'main', table: `t${String(i).padStart(2, '0')}`,
      column: 'oid', ref_table: 'orders', ref_column: 'id',
    }))
    const g = buildGraph('orders', 'orders', centerCols, many, colsOf)
    expect(g.overflow.left).toBe(2)
    expect(g.nodes.filter(n => n.side === 'left')).toHaveLength(20)
    expect(g.edges).toHaveLength(20)
    // 按 label 排序裁掉尾部:t20/t21 被裁
    expect(g.nodes.some(n => n.key === 't20')).toBe(false)
  })

  it('跨 schema 同名表 + 同名约束:两张邻居都保留(塌缩键带 schema)', () => {
    const rels: TableRelation[] = ['tenant_a', 'tenant_b'].map(s => ({
      name: 'orders_user_id_fkey', direction: 'in', schema: s, table: 'orders',
      column: 'user_id', ref_schema: 'public', ref_table: 'users', ref_column: 'id', seq: 0,
    }))
    const g = buildGraph('public.users', 'users', [], rels, () => [])
    expect(g.nodes.filter(n => n.side === 'left').map(n => n.key).sort())
      .toEqual(['tenant_a.orders', 'tenant_b.orders'])
    expect(g.edges).toHaveLength(2)
    expect(g.overflow.left).toBe(0)
  })

  it('columnsOf 未命中 → 空列卡片(只画表头)', () => {
    const g = buildGraph('orders', 'orders', centerCols, [rel({})], () => undefined)
    expect(g.nodes.find(n => n.key === 'users')!.columns).toEqual([])
  })
})

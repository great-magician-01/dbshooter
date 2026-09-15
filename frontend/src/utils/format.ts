/** 展示层小工具。 */

export function formatMs(ms: number | null | undefined): string {
  if (ms == null) return ''
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

/** 单元格值 → 显示文本;null 单独标记 */
export function cellText(v: any): string {
  if (v === null || v === undefined) return 'NULL'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

export function isNullCell(v: any): boolean {
  return v === null || v === undefined
}

/** 简易 SQL 格式化:关键字大写 + 主要子句换行;字符串字面量内的内容不动。 */
const CLAUSES = ['SELECT', 'FROM', 'WHERE', 'GROUP BY', 'ORDER BY', 'LIMIT',
                 'LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN', 'JOIN', 'HAVING', 'UNION']

export function formatSql(input: string): string {
  // 按字符串字面量切段,只在非字符串段做关键字处理
  const segments = input.split(/('(?:[^'\\]|\\.)*')/g)
  const out = segments.map((seg, i) => {
    if (i % 2 === 1) return seg
    let s = seg.replace(/\s+/g, ' ')
    for (const kw of CLAUSES) {
      const re = new RegExp(`\\b${kw.replace(' ', '\\s+')}\\b`, 'gi')
      s = s.replace(re, `\n${kw}`)
    }
    return s
  }).join('')
  return out.replace(/^\n/, '').split('\n').map(l => l.trimEnd()).join('\n').trim()
}

/** 从 AI 回答中提取 SQL(与后端 extract_sql 同规则,用于前端兜底展示) */
export function extractSql(text: string): string | null {
  const m = /```(?:sql)?\s*\n([\s\S]*?)```/i.exec(text)
  if (m) return m[1].trim()
  const head = text.trim().split(/\s/, 1)[0]?.toLowerCase()
  return head === 'select' || head === 'with' ? text.trim() : null
}

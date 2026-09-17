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

/** 简易 SQL 格式化:关键字大写 + 主要子句换行;字符串/标识符/注释内原样保留。 */
const CLAUSES = ['SELECT', 'FROM', 'WHERE', 'GROUP BY', 'ORDER BY', 'LIMIT', 'HAVING', 'UNION']

/** 连接词与 JOIN 一起处理,避免 "\nLEFT JOIN" 再被 JOIN 规则拆成两行 */
const JOIN_RE = /\b(?:(?:left|right|inner|outer|cross|full|natural)\s+)?join\b/gi

/** 需要原样保留(不做关键字改写)的区段 */
interface Seg { text: string; keep: boolean }

/** 扫描器:切出字符串 / 引号标识符 / 行注释 / 块注释 / $$ 块,其余为可格式化文本。
 *  与 backend/app/drivers/sqlutil.py 的 split_sql 同规则,保证前后端对同一段 SQL 的理解一致。 */
function splitProtected(input: string): Seg[] {
  const out: Seg[] = []
  let buf = ''
  let i = 0
  const n = input.length
  const flush = () => { if (buf) { out.push({ text: buf, keep: false }); buf = '' } }
  const take = (stop: number) => {
    flush()
    out.push({ text: input.slice(i, stop), keep: true })
    i = stop
  }
  while (i < n) {
    const ch = input[i]
    const nxt = i + 1 < n ? input[i + 1] : ''
    if (ch === '$' && nxt === '$') {              // PG 函数体
      const end = input.indexOf('$$', i + 2)
      take(end < 0 ? n : end + 2)
      continue
    }
    if (ch === "'" || ch === '"' || ch === '`') { // 字符串字面量 / 引号标识符
      let j = i + 1
      while (j < n) {
        const c = input[j]
        if (c === '\\' && ch !== '`') { j += 2; continue }
        if (c === ch) {
          if (input[j + 1] === ch) { j += 2; continue }   // '' / "" 转义
          j += 1
          break
        }
        j += 1
      }
      take(Math.min(j, n))
      continue
    }
    if (ch === '-' && nxt === '-') {              // 行注释:连换行一起保留,避免后续文本被注释吞掉
      const end = input.indexOf('\n', i)
      take(end < 0 ? n : end + 1)
      continue
    }
    if (ch === '/' && nxt === '*') {              // 块注释
      const end = input.indexOf('*/', i + 2)
      take(end < 0 ? n : end + 2)
      continue
    }
    buf += ch
    i += 1
  }
  flush()
  return out
}

/** 可格式化文本段:折叠空白 + 关键字大写并换行 */
function formatPlain(text: string): string {
  let s = text.replace(/\s+/g, ' ')
  s = s.replace(JOIN_RE, m => `\n${m.replace(/\s+/g, ' ').toUpperCase()}`)
  for (const kw of CLAUSES) {
    const re = new RegExp(`\\b${kw.replace(' ', '\\s+')}\\b`, 'gi')
    s = s.replace(re, `\n${kw}`)
  }
  return s.split('\n').map(l => l.trimEnd()).join('\n')
}

export function formatSql(input: string): string {
  let out = ''
  for (const seg of splitProtected(input)) {
    const text = seg.keep ? seg.text : formatPlain(seg.text)
    // 前一段已收在行尾(如行注释),这里不要再顶出一个空行
    out += out.endsWith('\n') && text.startsWith('\n') ? text.replace(/^\n+/, '') : text
  }
  return out.replace(/^\n+/, '').trim()
}

/** 从 AI 回答中提取 SQL(与后端 extract_sql 同规则,用于前端兜底展示) */
export function extractSql(text: string): string | null {
  const m = /```(?:sql)?\s*\n([\s\S]*?)```/i.exec(text)
  if (m) return m[1].trim()
  const head = text.trim().split(/\s/, 1)[0]?.toLowerCase()
  return head === 'select' || head === 'with' ? text.trim() : null
}

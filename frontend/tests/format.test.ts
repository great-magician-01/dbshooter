/** utils/format 纯函数单测。 */
import { describe, expect, it } from 'vitest'

import { cellText, extractSql, formatMs, formatSql, isNullCell } from '@/utils/format'

describe('formatMs', () => {
  it('毫秒与秒', () => {
    expect(formatMs(42)).toBe('42 ms')
    expect(formatMs(1500)).toBe('1.50 s')
    expect(formatMs(null)).toBe('')
  })
})

describe('cellText / isNullCell', () => {
  it('NULL 判定', () => {
    expect(isNullCell(null)).toBe(true)
    expect(isNullCell(undefined)).toBe(true)
    expect(isNullCell(0)).toBe(false)
    expect(cellText(null)).toBe('NULL')
    expect(cellText({ a: 1 })).toBe('{"a":1}')
    expect(cellText('x')).toBe('x')
  })
})

describe('formatSql', () => {
  it('主要子句换行并大写', () => {
    const out = formatSql("select id from users where city='上海' order by id limit 10")
    expect(out).toContain('SELECT')
    expect(out).toContain('\nFROM')
    expect(out).toContain('\nWHERE')
    expect(out).toContain('\nORDER BY')
    expect(out).toContain('\nLIMIT')
  })
  it('字符串内的关键字不受影响(简单场景)', () => {
    const out = formatSql("select 'from' from t")
    expect(out).toContain("'from'")
  })
})

describe('extractSql', () => {
  it('提取代码块', () => {
    expect(extractSql('好的:\n```sql\nSELECT 1;\n```')).toBe('SELECT 1;')
  })
  it('整体是 SQL 时原样返回', () => {
    expect(extractSql('SELECT 2')).toBe('SELECT 2')
  })
  it('无 SQL 返回 null', () => {
    expect(extractSql('无法回答')).toBeNull()
  })
})

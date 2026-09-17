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
  it('双引号字符串 / 引号标识符内的关键字保持原样', () => {
    const out = formatSql('select "from" from t')
    expect(out).toContain('"from"')
    expect(out).not.toContain('"FROM"')
  })
  it('MySQL 反引号标识符内的关键字保持原样', () => {
    const out = formatSql('select `from` from `where`')
    expect(out).toContain('`from`')
    expect(out).toContain('`where`')
  })
  it('单引号内的转义不影响后续解析', () => {
    const out = formatSql("select '它''s from here' from t")
    expect(out).toContain("'它''s from here'")
    expect(out).toContain('\nFROM t')
  })
  it('行注释里的关键字不换行,换行终止符被保留', () => {
    const out = formatSql('select 1 -- 注释 from t\nfrom t2')
    expect(out).toContain('-- 注释 from t')
    expect(out).toContain('\nFROM t2')
    // 注释后的代码不会被并进注释里
    expect(out.split('--')[1].split('\n')[0]).toBe(' 注释 from t')
  })
  it('块注释原样保留', () => {
    const out = formatSql('select 1 /* from join where */ from t')
    expect(out).toContain('/* from join where */')
    expect(out).toContain('\nFROM t')
  })
  it('$$ 块(PG 函数体)内不格式化', () => {
    const out = formatSql('select $$ select * from t where 1=1 $$ from x')
    expect(out).toContain('$$ select * from t where 1=1 $$')
    expect(out).toContain('\nFROM x')
  })
  it('JOIN 连接词不被拆成两行', () => {
    const out = formatSql('select * from a left join b on a.id=b.id')
    expect(out).toContain('\nLEFT JOIN b')
  })
  it('字符串内的分号与注释符不被误判', () => {
    const out = formatSql("select '-- 不是注释' from t")
    expect(out).toContain("'-- 不是注释'")
    expect(out).toContain('\nFROM t')
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

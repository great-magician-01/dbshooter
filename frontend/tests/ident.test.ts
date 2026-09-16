/** utils/ident 纯函数单测。 */
import { describe, expect, it } from 'vitest'

import { qualifiedTable, quoteIdent } from '@/utils/ident'

describe('quoteIdent', () => {
  it('PG/SQLite 用双引号并转义', () => {
    expect(quoteIdent('pg', 'users')).toBe('"users"')
    expect(quoteIdent('sqlite', 'a"b')).toBe('"a""b"')
  })

  it('MySQL 用反引号并转义', () => {
    expect(quoteIdent('mysql', 'users')).toBe('`users`')
    expect(quoteIdent('mysql', 'a`b')).toBe('`a``b`')
  })
})

describe('qualifiedTable', () => {
  it('PG 取路径末两段(忽略数据库段)', () => {
    expect(qualifiedTable('pg', 'chat_db.any_llm.balance_snapshots'))
      .toBe('"any_llm"."balance_snapshots"')
    expect(qualifiedTable('pg', 'mydb.public.users')).toBe('"public"."users"')
  })

  it('MySQL 全段限定', () => {
    expect(qualifiedTable('mysql', 'shop.users')).toBe('`shop`.`users`')
  })

  it('SQLite 全段限定(main 显式限定合法)', () => {
    expect(qualifiedTable('sqlite', 'main.users')).toBe('"main"."users"')
  })

  it('空段被忽略', () => {
    expect(qualifiedTable('pg', 'db..users')).toBe('"db"."users"')
  })
})

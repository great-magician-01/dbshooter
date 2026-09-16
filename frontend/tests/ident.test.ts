/** utils/ident 纯函数单测。 */
import { describe, expect, it } from 'vitest'

import { qualifiedTable, quoteIdent, schemaOfNode } from '@/utils/ident'

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

describe('schemaOfNode', () => {
  it('PG:两段及以上路径取 schema 段(第 2 段)', () => {
    expect(schemaOfNode('pg', { path: 'demo.sales' })).toBe('sales')
    expect(schemaOfNode('pg', { path: 'demo.sales.users' })).toBe('sales')
    expect(schemaOfNode('pg', { path: 'demo.sales.users.id' })).toBe('sales')
  })

  it('PG:数据库节点(单段路径)不绑定', () => {
    expect(schemaOfNode('pg', { path: 'demo' })).toBeUndefined()
  })

  it('非 PG 数据库一律不绑定', () => {
    expect(schemaOfNode('mysql', { path: 'shop' })).toBeUndefined()
    expect(schemaOfNode('sqlite', { path: 'main.users' })).toBeUndefined()
  })
})

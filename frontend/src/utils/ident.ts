/** SQL 标识符引用工具:把连接树上的元数据路径转成可执行的限定表名。 */

/** 按驱动类型给单个标识符加引号(PG/SQLite 双引号,MySQL 反引号),内部引号转义。 */
export function quoteIdent(connType: string, name: string): string {
  if (connType === 'mysql') return `\`${name.replace(/`/g, '``')}\``
  return `"${name.replace(/"/g, '""')}"`
}

/**
 * 元数据路径 → 带引号的限定表名。
 * PG 路径为 db.schema.table,取末两段(PG 不支持跨库引用,且 search_path 默认只含 public);
 * MySQL/SQLite 为 db.table,全段带上(显式限定 `main` 在 SQLite 同样合法)。
 */
export function qualifiedTable(connType: string, path: string): string {
  const segs = path.split('.').filter(Boolean)
  const pick = connType === 'pg' ? segs.slice(-2) : segs
  return pick.map(s => quoteIdent(connType, s)).join('.')
}

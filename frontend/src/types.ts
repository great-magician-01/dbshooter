/** 与后端对应的共享类型。 */

export type DbType = 'sqlite' | 'mysql' | 'pg' | 'redis' | 'mongo'
export type TabType = 'sql' | 'data' | 'redis' | 'mongo'

export interface Connection {
  id: string
  name: string
  type: DbType
  host: string
  port: number | null
  database: string
  username: string
  has_password: boolean
  params: Record<string, any>
  readonly: boolean
}

export interface ConnectionSave {
  id?: string | null
  name: string
  type: DbType | string
  host?: string
  port?: number | null
  database?: string
  username?: string
  password?: string
  params?: Record<string, any>
  readonly?: boolean
}

export interface MetaNode {
  path: string
  label: string
  kind: 'database' | 'schema' | 'table' | 'view' | 'column' | 'keygroup' | 'key' | 'collection' | 'index'
  has_children: boolean
  extra: Record<string, any>
}

export interface Column { name: string; type: string }

export interface ExecResult {
  kind: 'rows' | 'affected' | 'command' | 'documents'
  columns: Column[]
  rows: any[][]
  affected?: number | null
  elapsed_ms?: number
  truncated?: boolean
  error?: string | null
  raw?: any
}

export interface Tab {
  id: string
  type: TabType
  title: string
  connection_id: string | null
  context: Record<string, any>
  content: string
  sort: number
  is_active?: boolean
}

export interface AiProvider {
  id: string
  name: string
  base_url: string
  model: string
  has_api_key: boolean
  is_active: boolean
}

export interface AiProviderSave {
  id?: string | null
  name: string
  base_url: string
  api_key?: string
  model: string
}

export interface AiSession {
  id: string
  title: string
  connection_id: string | null
  updated_at?: string
}

export interface AiMessage {
  id?: string
  role: 'user' | 'assistant'
  /** assistant 为 {text, sql} JSON 解析后的结构 */
  text: string
  sql: string | null
  streaming?: boolean
  elapsed_ms?: number | null
}

export interface WsEvent {
  id: string
  event: string
  data: any
}

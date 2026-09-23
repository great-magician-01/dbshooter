/** 与后端对应的共享类型。 */

export type DbType = 'sqlite' | 'mysql' | 'pg' | 'redis' | 'mongo'
export type TabType = 'sql' | 'data' | 'redis' | 'mongo' | 'table'

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
  /** error 为前端本地节点:子节点加载失败时占位显示,不可展开(见 TreeNode.vue) */
  kind: 'database' | 'schema' | 'table' | 'view' | 'column' | 'keygroup' | 'key' | 'collection'
      | 'index' | 'error'
  has_children: boolean
  extra: Record<string, any>
}

export interface Column { name: string; type: string }

/** 表详情-结构页签:单列元数据(对应后端 drivers/base.py ColumnInfo) */
export interface ColumnInfo {
  name: string
  type: string
  nullable: boolean
  default: string | null
  /** 0=非主键;1..n=主键内序号(复合主键按索引列序) */
  pk: number
  ordinal: number
  comment: string
  /** pg:'' | 's'(存储生成列,default 为其生成表达式) */
  generated?: string
  /** pg:'' | 'a'(GENERATED ALWAYS) | 'd'(BY DEFAULT) */
  identity?: string
}

/** 表详情-ER 页签:一条外键列对(对应后端 RelationInfo;direction 相对被查询表) */
export interface TableRelation {
  name: string
  direction: 'out' | 'in'
  schema: string
  table: string
  column: string
  ref_schema: string
  ref_table: string
  ref_column: string
  seq: number
}

export interface ExecResult {
  kind: 'rows' | 'affected' | 'command' | 'documents' | 'error'
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

export interface AiToolTrace {
  call_id?: string
  name: string
  args?: string
  status: 'running' | 'done' | 'error'
  summary?: string
}

export interface AiMessage {
  id?: string
  role: 'user' | 'assistant'
  /** assistant 为 {text, sql, tools} JSON 解析后的结构 */
  text: string
  sql: string | null
  tools?: AiToolTrace[]
  streaming?: boolean
  elapsed_ms?: number | null
}

/** 单条语句的执行汇总(query.done 的 summary 元素) */
export interface QuerySummary {
  kind: 'rows' | 'affected' | 'command' | 'documents' | string
  affected: number | null
  error: string | null
}

/** query.done 事件数据(见 backend/app/services/query_service.py) */
export interface QueryDone {
  query_id: string
  row_count: number
  elapsed_ms: number
  truncated?: boolean
  summary?: QuerySummary[]
  error?: string | null
}

export interface WsEvent {
  id: string
  event: string
  data: any
}

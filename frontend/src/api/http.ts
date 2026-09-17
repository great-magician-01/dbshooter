/** REST 客户端:统一错误处理 + 可选访问令牌。只用 GET/POST(见设计文档 §6)。 */
import axios, { AxiosError } from 'axios'

/** 带 HTTP 状态码的错误:调用方据此区分 401(令牌失配)/ 422(参数校验)等 */
export interface HttpError extends Error {
  status?: number
}

/** FastAPI 的 detail 可能是字符串,也可能是校验错误数组([{loc, msg, type}...]) */
function detailToText(detail: any): string | null {
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail)) {
    // 数组时只取 msg:否则前端只能看到 axios 的 "Request failed with status code 422"
    const parts = detail
      .map(d => (typeof d === 'string' ? d : d?.msg))
      .filter((s): s is string => typeof s === 'string' && s.length > 0)
    if (parts.length) return parts.join(';')
  }
  return null
}

export const http = axios.create({ baseURL: '/', timeout: 30000 })

http.interceptors.request.use((cfg) => {
  const token = localStorage.getItem('ds-token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

http.interceptors.response.use(
  (resp) => resp,
  async (err: AxiosError<any>) => {
    let detail = err.response?.data?.detail
    const data = err.response?.data
    // responseType: 'blob' 的请求出错时响应体是 Blob,detail 藏在里面
    if (typeof Blob !== 'undefined' && data instanceof Blob) {
      try { detail = JSON.parse(await data.text())?.detail } catch { /* 非 JSON 错误体 */ }
    }
    const msg = detailToText(detail) ?? err.message
    const e = new Error(msg || '请求失败') as HttpError
    e.status = err.response?.status
    return Promise.reject(e)
  },
)

export async function get<T>(url: string, params?: Record<string, any>): Promise<T> {
  return (await http.get<T>(url, { params })).data
}

export async function post<T>(url: string, body?: any): Promise<T> {
  return (await http.post<T>(url, body)).data
}

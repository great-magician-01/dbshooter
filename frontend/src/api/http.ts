/** REST 客户端:统一错误处理 + 可选访问令牌。只用 GET/POST(见设计文档 §6)。 */
import axios, { AxiosError } from 'axios'

export const http = axios.create({ baseURL: '/', timeout: 30000 })

http.interceptors.request.use((cfg) => {
  const token = localStorage.getItem('ds-token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

http.interceptors.response.use(
  (resp) => resp,
  (err: AxiosError<any>) => {
    const detail = err.response?.data?.detail
    const msg = typeof detail === 'string' ? detail : err.message
    return Promise.reject(new Error(msg || '请求失败'))
  },
)

export async function get<T>(url: string, params?: Record<string, any>): Promise<T> {
  return (await http.get<T>(url, { params })).data
}

export async function post<T>(url: string, body?: any): Promise<T> {
  return (await http.post<T>(url, body)).data
}

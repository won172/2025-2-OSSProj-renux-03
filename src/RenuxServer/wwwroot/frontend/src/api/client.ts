/**
 * Lightweight API client wrapper around fetch with JSON helpers.
 */
import { Capacitor, CapacitorHttp } from '@capacitor/core'

export interface ApiRequestOptions extends RequestInit {
  json?: Record<string, unknown> | unknown[] | null
}

export interface ApiError extends Error {
  status?: number
  details?: unknown
}

const defaultHeaders = {
  Accept: 'application/json',
}

const configuredApiBaseUrl =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)
  || (import.meta.env.VITE_DEV_SERVER_PROXY_TARGET as string | undefined)
  || ''

const buildRequestUrl = (input: RequestInfo) => {
  if (typeof input !== 'string') {
    return input
  }

  if (!configuredApiBaseUrl || /^https?:\/\//i.test(input)) {
    return input
  }

  if (!input.startsWith('/')) {
    return input
  }

  return `${configuredApiBaseUrl.replace(/\/$/, '')}${input}`
}

const isNgrokUrl = (value: string) => {
  try {
    const url = new URL(value)
    return /(?:^|\.)ngrok(?:-free)?\.(?:app|dev)$/i.test(url.hostname)
  } catch {
    return false
  }
}

/**
 * apiFetch와 동일한 규칙으로 경로를 절대 URL로 해석한다.
 * SSE 스트리밍처럼 fetch를 직접 써야 하는 곳에서 base URL 로직을 재사용하기 위함.
 */
export const resolveApiUrl = (path: string) => buildRequestUrl(path) as string

/** 해당 URL이 ngrok 도메인이면 경고 우회 헤더를 더한 객체를 반환한다. */
export const withNgrokHeader = (url: string, headers: Record<string, string>) =>
  isNgrokUrl(url) ? { ...headers, 'ngrok-skip-browser-warning': 'true' } : headers

const parseJson = async (response: Response) => {
  const text = await response.text()
  if (!text) return undefined
  try {
    return JSON.parse(text)
  } catch (error) {
    console.error('Failed to parse JSON response', error)
    return undefined
  }
}

/**
 * WKWebView treats a remote API cookie as a third-party cookie when the app
 * itself is loaded from capacitor://localhost.  Use Capacitor's native HTTP
 * bridge for JSON requests so URLSession owns the cookie jar and synchronizes
 * Set-Cookie back to the WebView.  Streaming endpoints intentionally keep
 * using fetch because the native HTTP bridge buffers the full response.
 */
const canUseNativeHttp = (requestUrl: RequestInfo, body: BodyInit | null | undefined) =>
  Capacitor.isNativePlatform()
  && typeof requestUrl === 'string'
  && /^https?:\/\//i.test(requestUrl)
  && (body == null || typeof body === 'string')

const throwApiError = (status: number, details: unknown): never => {
  const error: ApiError = new Error(`요청이 실패했습니다. (Status: ${status})`)
  error.status = status
  error.details = details
  throw error
}

export const apiFetch = async <TResponse = unknown>(input: RequestInfo, options: ApiRequestOptions = {}) => {
  const { json, headers, ...rest } = options
  const requestUrl = buildRequestUrl(input)
  const resolvedHeaders = {
    ...defaultHeaders,
    ...headers,
  } as Record<string, string>

  if (json !== undefined) {
    resolvedHeaders['Content-Type'] = 'application/json'
  }

  if (typeof requestUrl === 'string' && isNgrokUrl(requestUrl)) {
    resolvedHeaders['ngrok-skip-browser-warning'] = 'true'
  }

  const init: RequestInit = {
    ...rest,
    credentials: 'include',
    headers: resolvedHeaders,
  }

  if (json !== undefined) {
    init.body = JSON.stringify(json)
  }

  if (canUseNativeHttp(requestUrl, init.body)) {
    const nativeResponse = await CapacitorHttp.request({
      url: requestUrl as string,
      method: String(rest.method ?? 'GET').toUpperCase(),
      headers: resolvedHeaders,
      ...(init.body !== undefined ? { data: init.body } : {}),
      responseType: 'json',
    })

    if (nativeResponse.status < 200 || nativeResponse.status >= 300) {
      throwApiError(nativeResponse.status, nativeResponse.data)
    }

    return nativeResponse.data as TResponse
  }

  const response = await fetch(requestUrl, init)

  let parsedBody: unknown
  try {
    parsedBody = await parseJson(response)
  } catch (error) {
    console.error('Error parsing response body', error)
  }

  if (!response.ok) {
    throwApiError(response.status, parsedBody)
  }

  return parsedBody as TResponse
}

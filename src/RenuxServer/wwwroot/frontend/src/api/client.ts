/**
 * Lightweight API client wrapper around fetch with JSON helpers.
 */
export interface ApiRequestOptions extends RequestInit {
  json?: Record<string, unknown> | unknown[] | null
}

export interface ApiError extends Error {
  status?: number
  details?: unknown
}

const defaultHeaders = {
  'Content-Type': 'application/json',
}

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

export const apiFetch = async <TResponse = unknown>(input: RequestInfo, options: ApiRequestOptions = {}) => {
  const { json, headers, ...rest } = options
  const init: RequestInit = {
    ...rest,
    headers: {
      ...defaultHeaders,
      ...headers,
    },
  }

  if (json !== undefined) {
    init.body = JSON.stringify(json)
  }

  const response = await fetch(input, init)

  let parsedBody: unknown
  try {
    parsedBody = await parseJson(response)
  } catch (error) {
    console.error('Error parsing response body', error)
  }

  if (!response.ok) {
    const error: ApiError = new Error('요청이 실패했습니다.')
    error.status = response.status
    error.details = parsedBody
    throw error
  }

  return parsedBody as TResponse
}

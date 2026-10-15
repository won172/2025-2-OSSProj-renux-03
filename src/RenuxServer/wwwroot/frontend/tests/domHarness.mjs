// Minimal document for state/effect tests. This does not model CSS, layout or a browser.
import { DOMImplementation } from '@xmldom/xmldom'
import { flushSync } from 'react-dom'

export function testDocument() {
  const document = new DOMImplementation().createDocument('http://www.w3.org/1999/xhtml', 'html')
  const Element = document.documentElement.constructor
  const create = document.createElement.bind(document)
  const createNS = document.createElementNS.bind(document)
  const listeners = new Map()
  const match = (node, selector) => {
    if (node.nodeType !== 1) return false
    let base = selector.trim()
    const nots = [...base.matchAll(/:not\(([^)]+)\)/g)].map((part) => part[1])
    base = base.replace(/:not\([^)]+\)/g, '')
    if (nots.some((part) => match(node, part))) return false
    const tag = base.match(/^[a-z][\w-]*/i)?.[0]
    if (tag && node.tagName.toLowerCase() !== tag.toLowerCase()) return false
    for (const [, cls] of base.matchAll(/\.([\w-]+)/g)) if (!node.getAttribute('class')?.split(' ').includes(cls)) return false
    for (const [, name, , value] of base.matchAll(/\[([^=\]]+)(=(?:"([^"]*)"|'[^']*'|[^\]]*))?\]/g)) {
      if (!node.hasAttribute(name)) return false
      if (value !== undefined && node.getAttribute(name) !== value) return false
    }
    return true
  }
  const init = (el) => {
    if (el.tagName.toLowerCase() === 'select') Object.defineProperty(el, 'options', { get: () => Array.from(el.getElementsByTagName('option')) })
    if (el.tagName.toLowerCase() === 'option') Object.defineProperty(el, 'value', { get: () => el.getAttribute('value') ?? el.textContent, set: (value) => el.setAttribute('value', value) })
    el.style ??= { setProperty(key, value) { this[key] = value }, removeProperty(key) { delete this[key] } }
    el.addEventListener = () => {}; el.removeEventListener = () => {}
    el.querySelectorAll = (selector) => Array.from(el.getElementsByTagName('*')).filter((child) => selector.split(',').some((part) => match(child, part)))
    el.querySelector = (selector) => el.querySelectorAll(selector)[0] ?? null
    el.contains = (node) => { while (node) { if (node === el) return true; node = node.parentNode } return false }
    el.closest = (selector) => { let node = el; while (node?.nodeType === 1) { if (selector.split(',').some((part) => match(node, part))) return node; node = node.parentNode } return null }
    el.focus = () => { document.activeElement = el; el.focusCount = (el.focusCount ?? 0) + 1 }
    el.scrollIntoView = () => { el.scrollCount = (el.scrollCount ?? 0) + 1 }
    el.getClientRects = () => el.closest('[hidden]') ? [] : [{}]
    Object.defineProperty(el, 'isConnected', { get: () => document.documentElement.contains(el), configurable: true })
    return el
  }
  document.createElement = (...args) => init(create(...args))
  document.createElementNS = (...args) => init(createNS(...args))
  init(document.documentElement)
  document.body = document.createElement('body'); document.documentElement.appendChild(document.body)
  document.activeElement = document.body
  document.addEventListener = (type, callback) => { const set = listeners.get(type) ?? new Set(); set.add(callback); listeners.set(type, set) }
  document.removeEventListener = (type, callback) => listeners.get(type)?.delete(callback)
  const windowListeners = new Map()
  const window = {
    document, HTMLElement: Element, HTMLIFrameElement: class {},
    location: { href: 'http://localhost/', origin: 'http://localhost' },
    matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }),
    setTimeout, clearTimeout, requestAnimationFrame: (fn) => setTimeout(fn, 0), cancelAnimationFrame: clearTimeout,
    addEventListener: (type, cb) => { const set = windowListeners.get(type) ?? new Set(); set.add(cb); windowListeners.set(type, set) },
    removeEventListener: (type, cb) => windowListeners.get(type)?.delete(cb),
  }
  document.defaultView = window
  const originals = new Map(['window', 'document', 'HTMLElement'].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  Object.assign(globalThis, { window, document, HTMLElement: Element })
  const container = document.createElement('div'); document.body.appendChild(container)
  return { document, window, container, listeners, windowListeners, restore() {
    for (const [key, descriptor] of originals) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key] }
  } }
}
export const props = (element) => element[Object.keys(element).find((key) => key.startsWith('__reactProps$'))]
export const click = (element) => flushSync(() => props(element).onClick?.({ preventDefault() {}, stopPropagation() {}, currentTarget: element, target: element }))
export const change = (element, value) => flushSync(() => props(element).onChange({ target: { value }, currentTarget: element }))
export const settle = async () => { for (let index = 0; index < 8; index++) { await new Promise(setImmediate); flushSync(() => {}) } }
export const button = (container, text) => Array.from(container.getElementsByTagName('button')).find((node) => node.textContent.trim() === text)

#!/usr/bin/env node
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const source = readFileSync(new URL('../public/js/asr-settings-modern.js', import.meta.url), 'utf8')
const listeners = (element, type) => element.listeners[type] || []

class ClassList {
  constructor(values = []) { this.values = new Set(values) }
  contains(value) { return this.values.has(value) }
  toggle(value, enabled) { enabled ? this.values.add(value) : this.values.delete(value) }
}

class Element {
  constructor(classes = []) {
    this.classList = new ClassList(classes)
    this.dataset = {}; this.hidden = false; this.children = []; this.listeners = {}; this.attributes = {}
    this.parentNode = null; this.tabIndex = 0; this.focused = false
  }
  set className(value) { this.classList = new ClassList(String(value).split(/\s+/).filter(Boolean)) }
  get className() { return [...this.classList.values].join(' ') }
  matches(selectors) { return selectors.split(',').some((selector) => this.classList.contains(selector.trim().replace(/^\./, ''))) }
  setAttribute(name, value) { this.attributes[name] = String(value) }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener) }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child }
  insertBefore(child, before) { child.parentNode = this; const at = this.children.indexOf(before); at < 0 ? this.children.push(child) : this.children.splice(at, 0, child); return child }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((child) => child !== this) }
  focus() { this.focused = true }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null }
  querySelectorAll(selector) {
    const wanted = selector.replace(/^\./, '')
    const found = []
    const visit = (node) => { for (const child of node.children) { if (child.classList.contains(wanted)) found.push(child); visit(child) } }
    visit(this); return found
  }
  get firstChild() { return this.children[0] || null }
}

const documentStub = {
  body: { classList: { contains: () => false } },
  createElement: () => new Element(),
}
const windowStub = {}
vm.runInNewContext(source, { window: windowStub, document: documentStub, globalThis: windowStub })
const controller = windowStub.AsrSettingsBridgeTabs
if (!controller) throw new Error('Bridge tab controller was not exported for the browser.')

function bridgeScope(mode) {
  const scope = new Element(['asr-bridge-panel-body'])
  const classNames = ['asr-card-basics-section', 'asr-standard-bridge-settings', 'asr-backend-readiness-section', 'asr-bridge-advanced-section']
  if (mode !== 'dmr') classNames.push('asr-connected-client-settings')
  if (mode === 'dmr') classNames.push('asr-standard-dmr-tgif')
  for (const name of classNames) {
    const section = new Element(['asr-bridge-panel-section', name])
    section.dataset.bridgeTabLabel = name === 'asr-standard-bridge-settings' ? 'Link Recovery'
      : name === 'asr-standard-dmr-tgif' ? 'TGIF Sessions'
      : name === 'asr-connected-client-settings' ? (mode === 'zello' ? 'Recent Talkers' : 'Client Data')
      : name === 'asr-backend-readiness-section' ? 'Status'
      : name === 'asr-bridge-advanced-section' ? 'Advanced' : 'Basics'
    scope.appendChild(section)
  }
  return scope
}

for (const mode of ['zello', 'dmr', 'ysf', 'p25', 'nxdn', 'm17']) {
  const scope = bridgeScope(mode)
  controller.install(scope, documentStub)
  const buttons = scope.querySelectorAll('.asr-bridge-editor-tab')
  const names = buttons.map((button) => button.dataset.bridgeTab)
  const expected = ['basics', 'controls', 'clients', 'diagnostics', 'advanced']
  if (JSON.stringify(names) !== JSON.stringify(expected)) throw new Error(`${mode}: wrong live tabs: ${names.join(',')}`)
  const expectedClientLabel = mode === 'dmr' ? 'TGIF Sessions' : mode === 'zello' ? 'Recent Talkers' : 'Client Data'
  const clientButton = buttons.find((button) => button.dataset.bridgeTab === 'clients')
  if (expectedClientLabel && (!clientButton || clientButton.textContent !== expectedClientLabel)) throw new Error(`${mode}: wrong client tab label`)
  for (const button of buttons) {
    for (const click of listeners(button, 'click')) click({ target: button, preventDefault() {} })
    if (!button.classList.contains('is-active') || button.attributes['aria-selected'] !== 'true') throw new Error(`${mode}: ${button.dataset.bridgeTab} did not activate`)
    const visible = scope.querySelectorAll('.asr-bridge-panel-section').filter((section) => section.dataset.modernTabHidden === 'false')
    if (!visible.length || visible.some((section) => controller.sectionTab(section) !== button.dataset.bridgeTab)) throw new Error(`${mode}: wrong content for ${button.dataset.bridgeTab}`)
    if (scope.parentNode !== null) throw new Error(`${mode}: tab navigation moved or closed the modal content`)
  }
  const tablist = scope.querySelector('.asr-bridge-editor-tabs')
  const active = buttons.find((button) => button.classList.contains('is-active'))
  for (const keydown of listeners(tablist, 'keydown')) keydown({ key:'Home', target:active, preventDefault() {} })
  if (!buttons[0].classList.contains('is-active') || !buttons[0].focused) throw new Error(`${mode}: keyboard tab navigation failed`)
}

const noClients = bridgeScope('zello')
noClients.children.find((section) => section.classList.contains('asr-connected-client-settings')).hidden = true
controller.install(noClients, documentStub)
if (noClients.querySelectorAll('.asr-bridge-editor-tab').some((button) => button.dataset.bridgeTab === 'clients')) throw new Error('A dead Connected Clients tab was rendered.')

console.log('ASR bridge editor interactive tab self-test passed')

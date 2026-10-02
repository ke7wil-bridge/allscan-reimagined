#!/usr/bin/env node
import { readFileSync } from 'node:fs'
function assert(c,m){ if(!c) throw new Error(m) }
const app=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8')
const dialog=readFileSync(new URL('../src/components/SupportFeedbackDialog.tsx',import.meta.url),'utf8')
const menuFeedback=app.indexOf('className="allscan-menu-proxy-row allscan-menu-report-row"')
const menuLogout=app.indexOf('className="allscan-menu-proxy-row allscan-menu-logout-row"')
assert(menuFeedback>=0 && menuFeedback<menuLogout,'Support & Feedback must precede Logout in the shared menu')
assert(app.includes('<span>Support &amp; Feedback</span>'),'Support & Feedback menu label missing')
assert(app.includes('setSupportFeedbackOpen(true)'),'Support & Feedback menu does not open the shared dialog')
assert(app.includes('SupportFeedbackDialog config={config}'),'Support & Feedback dialog render site missing')
assert(dialog.includes('onClick={onClose}>Cancel</button>'),'Support & Feedback Cancel action missing')
assert(!app.includes('allscan-menu-support-row') && !app.includes('Support ASR</button>'),'obsolete standalone Support ASR menu action returned')
console.log('support/menu lock self-test: ok')

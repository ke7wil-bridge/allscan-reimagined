#!/usr/bin/env node
import { readFileSync, readdirSync } from 'node:fs'
import { strict as assert } from 'node:assert'
const css = readFileSync('src/styles/asr/09-responsive-layouts.css', 'utf8')
assert(!css.includes('.allscan-bridge-grid > .allscan-bridge-card:not(.allscan-net-bridge-card):nth-last-child(2):nth-child(odd)'), 'penultimate standard bridge card must not be centered separately')
assert(css.includes('grid-template-columns: repeat(2, minmax(0, 1fr))'), 'phone bridge grid must have two columns')
const html = readFileSync('dist/index.html', 'utf8')
const filename = html.match(/assets\/(index-[^" ]+\.css)/)?.[1]
assert(filename, 'Vite must reference a versioned CSS asset')
const builtCss = readFileSync(`dist/assets/${filename}`, 'utf8')
assert(!builtCss.includes('.allscan-bridge-grid>.allscan-bridge-card:not(.allscan-net-bridge-card):nth-last-child(2):nth-child(odd)'), 'production CSS must not contain regression selector')
assert(readdirSync('dist/assets').includes(filename), 'referenced versioned asset must exist')
console.log('PASS: six standard bridges share grid placement; production CSS hash referenced')

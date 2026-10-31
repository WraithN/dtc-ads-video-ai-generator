// Regression guard for the i18n key space.
//
// Catches the class of bug from the "需求 D" round: a `t('some.key')` call
// referencing a key that does not exist in the locale files. i18next silently
// renders the raw key in that case, which only shows up at runtime. TypeScript
// flags a missing key in `en` (because `en: typeof zh`), but it cannot see a
// key absent from BOTH locales, nor a key typo'd only at the call site -- that
// is exactly what this script checks.
//
// It also validates that dynamic template keys (`t(\`history.${x}\`)`) resolve
// against a namespace that actually exists, so a typo'd dynamic namespace is
// caught too.
//
// Run:  node web/frontend/scripts/i18n-check.mjs
// Exit: 0 if every referenced key exists, 1 otherwise.

import { transform } from 'esbuild'
import { readFileSync, writeFileSync, mkdirSync, rmSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const root = process.cwd()
const localesDir = join(root, 'src/i18n/locales')
const cacheDir = join(root, 'node_modules/.cache/i18n-check')

rmSync(cacheDir, { recursive: true, force: true })
mkdirSync(cacheDir, { recursive: true })

async function loadLocale(name) {
  const file = join(localesDir, name)
  const src = readFileSync(file, 'utf8')
  const out = await transform(src, { loader: 'ts', format: 'esm', sourcefile: file })
  const tmp = join(cacheDir, name.replace(/\.ts$/, '.mjs'))
  writeFileSync(tmp, out.code)
  const mod = await import(pathToFileURL(tmp).href)
  return mod.default
}

function dotPaths(obj, prefix = '') {
  const out = new Set()
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      for (const p of dotPaths(v, key)) out.add(p)
    } else {
      out.add(key)
    }
  }
  return out
}

function walk(dir, acc = []) {
  for (const e of readdirSync(dir)) {
    const p = join(dir, e)
    const s = statSync(p)
    if (s.isDirectory()) {
      if (e === 'node_modules' || e === '.cache') continue
      walk(p, acc)
    } else if (/\.(ts|tsx)$/.test(e)) {
      acc.push(p)
    }
  }
  return acc
}

// Matches the `t(` or `i18n.t(` call head; the rest of the args are parsed
// with STATIC / TEMPLATE below.
const CALL = /(?:\bi18n\.t|\bt)\(\s*/g
const STATIC = /^['"]([^'"]+)['"]\s*[,)]/
const TEMPLATE = /^`([^`]*)`\s*[,)]/

function scanSources() {
  const staticKeys = new Set()
  const dynamicPrefixes = new Set()
  for (const f of walk(join(root, 'src'))) {
    const text = readFileSync(f, 'utf8')
    let m
    CALL.lastIndex = 0
    while ((m = CALL.exec(text)) !== null) {
      const rest = text.slice(CALL.lastIndex)
      const sm = STATIC.exec(rest)
      if (sm) {
        const key = sm[1]
        if (key && key.trim()) staticKeys.add(key)
        continue
      }
      const tm = TEMPLATE.exec(rest)
      if (tm) {
        const content = tm[1]
        const idx = content.indexOf('${')
        if (idx === -1) {
          if (content.trim()) staticKeys.add(content)
        } else {
          const prefix = content.slice(0, idx)
          if (prefix.endsWith('.')) dynamicPrefixes.add(prefix.slice(0, -1))
          else if (prefix) dynamicPrefixes.add(prefix) // e.g. `abc${x}` -> "abc"
          // prefix empty (starts with ${...}) => fully dynamic, cannot check
        }
      }
    }
  }
  return { staticKeys, dynamicPrefixes }
}

function main(zh, en) {
  const errors = []
  const { staticKeys, dynamicPrefixes } = scanSources()

  // 1. Every static key used in code must exist in the source locale.
  for (const k of [...staticKeys].sort()) {
    if (!zh.has(k)) errors.push(`missing key in zh.ts: "${k}" (used in code)`)
  }

  // 2. Dynamic template prefixes must resolve against a real namespace.
  for (const p of [...dynamicPrefixes].sort()) {
    const ok = [...zh].some((k) => k === p || k.startsWith(p + '.'))
    if (!ok) errors.push(`dynamic key prefix has no matching namespace in zh.ts: "${p}.*"`)
  }

  // 3. zh and en must describe the same key set (sanity backstop for tsc).
  for (const k of [...zh].filter((k) => !en.has(k))) {
    errors.push(`key only in zh.ts (missing in en.ts): "${k}"`)
  }
  for (const k of [...en].filter((k) => !zh.has(k))) {
    errors.push(`key only in en.ts (missing in zh.ts): "${k}"`)
  }

  console.log(`locale keys: zh=${zh.size} en=${en.size}`)
  console.log(`static t() keys referenced in code: ${staticKeys.size}`)
  console.log(`dynamic t() prefixes referenced in code: ${dynamicPrefixes.size}`)

  if (errors.length) {
    console.error('\ni18n check FAILED:')
    for (const e of errors) console.error('  - ' + e)
    process.exit(1)
  }
  console.log('\ni18n check passed: every referenced key exists in both locales.')
}

;(async () => {
  const zh = dotPaths(await loadLocale('zh.ts'))
  const en = dotPaths(await loadLocale('en.ts'))
  main(zh, en)
})()

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

/**
 * Every class a component references must exist in the stylesheet.
 *
 * Alerts, Releases, Web Vitals and Cache all shipped referencing `.page-head` and `.table`,
 * neither of which was ever defined. Nothing failed: the pages rendered with browser defaults
 * — a 32px heading and a borderless table — and looked broken while every test passed, because
 * no test can see a stylesheet.
 *
 * A missing class is silent by construction, which is exactly why it needs a mechanical check.
 */
function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) return sources(path)
    return path.endsWith('.tsx') && !path.includes('.test.') ? [path] : []
  })
}

describe('Stylesheet', () => {
  it('defines every class the components use', () => {
    const css = readFileSync('src/styles.css', 'utf8')
    const defined = new Set([...css.matchAll(/\.([a-zA-Z][\w-]*)/g)].map((match) => match[1]))
    const missing = new Map<string, Set<string>>()

    for (const file of sources('src')) {
      const source = readFileSync(file, 'utf8')
      for (const match of source.matchAll(/className=(?:"([^"]+)"|\{`([^`]+)`\})/g)) {
        for (const name of `${match[1] ?? ''} ${match[2] ?? ''}`.split(/\s+/)) {
          // A template literal interpolates the modifier, so only the static part of such a
          // name is checkable and a bare `${…}` chunk is not a class at all.
          if (!name || !/^[a-zA-Z][\w-]*$/.test(name)) continue
          if (!defined.has(name)) missing.set(name, (missing.get(name) ?? new Set()).add(file))
        }
      }
    }

    expect([...missing].map(([name, files]) => `.${name} (${[...files].join(', ')})`)).toEqual([])
  })
})

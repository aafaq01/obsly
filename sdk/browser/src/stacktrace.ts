export interface Frame {
  filename: string
  function: string
  lineno: number | null
  colno: number | null
  in_app: boolean
}

// Chrome/Edge/Node:  "    at fn (https://host/app.js:1:2)"  or  "    at https://host/app.js:1:2"
const V8 =
  /^\s*at (?:(.+?)\s+\()?((?:file|https?|blob|chrome-extension|native|eval).*?):(\d+):(\d+)\)?\s*$/
// Firefox/Safari:    "fn@https://host/app.js:1:2"
const SPIDERMONKEY =
  /^\s*(?:(.*?)@)?((?:file|https?|blob|chrome-extension|resource).*?):(\d+):(\d+)\s*$/

/**
 * Parse a stack string into frames.
 *
 * Two engines, two formats, and no standard. Anything unrecognised is skipped rather than
 * guessed at: a wrong filename sends somebody to the wrong file, which costs more than the
 * frame being absent.
 */
export function parseStack(stack: string | undefined, origin: string): Frame[] {
  if (!stack) return []

  const frames: Frame[] = []
  for (const line of stack.split('\n').slice(0, 60)) {
    const match = V8.exec(line) ?? SPIDERMONKEY.exec(line)
    if (!match) continue

    const [, fn, filename, lineno, colno] = match
    if (!filename) continue

    frames.push({
      filename,
      function: fn ?? '<anonymous>',
      lineno: Number(lineno) || null,
      colno: Number(colno) || null,
      // Everything on the page's own origin is the application; a bundle served from a CDN is
      // somebody else's code, and marking it in_app buries the frame that matters.
      in_app: filename.startsWith(origin),
    })
  }

  // Oldest first, matching the server SDK — the culprit is derived from the last in_app frame,
  // and reversing one side would pick the entry point instead of the throw site.
  return frames.reverse()
}

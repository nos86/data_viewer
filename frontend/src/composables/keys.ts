/** Keyboard shortcuts (RF-39) as a pure mapping, so it can be tested without a DOM. */

export type KeyAction =
  | { type: 'moveCursor'; dk: number; edge: 'whole' | 'end' }
  | { type: 'zoom'; factor: number } // < 1 zooms in
  | { type: 'resetZoom' }
  | { type: 'applyZoomAll' }
  | { type: 'tab'; delta: 1 | -1 }
  | { type: 'tabIndex'; index: number }
  | { type: 'newCursor' }
  | { type: 'newBand' }
  | { type: 'removeCursor' }
  | { type: 'cycleCursor'; dir: 1 | -1 }
  | { type: 'goToCursor' }
  | { type: 'setT0' }
  | { type: 'help' }

export interface KeyLike {
  key: string
  code?: string
  shiftKey: boolean
  altKey: boolean
  ctrlKey: boolean
  metaKey: boolean
  target?: unknown
}

const TYPING = new Set(['INPUT', 'TEXTAREA', 'SELECT'])

export function keyAction(e: KeyLike): KeyAction | null {
  const t = e.target as { tagName?: string; isContentEditable?: boolean } | null | undefined
  if (t && (TYPING.has((t.tagName ?? '').toUpperCase()) || t.isContentEditable)) return null
  if (e.ctrlKey || e.metaKey) return null

  if (e.altKey && e.code && /^Digit[1-9]$/.test(e.code)) return { type: 'tabIndex', index: Number(e.code.slice(5)) - 1 }
  switch (e.key) {
    case 'ArrowLeft':
    case 'ArrowRight': {
      const dk = (e.key === 'ArrowLeft' ? -1 : 1) * (e.shiftKey ? 10 : 1)
      return { type: 'moveCursor', dk, edge: e.altKey ? 'end' : 'whole' }
    }
    case '+':
    case '=':
      return { type: 'zoom', factor: 0.5 }
    case '-':
    case '_':
      return { type: 'zoom', factor: 2 }
    case '0':
      return { type: 'resetZoom' }
    case 'a':
    case 'A':
      return { type: 'applyZoomAll' }
    case 'PageUp':
      return { type: 'tab', delta: -1 }
    case 'PageDown':
      return { type: 'tab', delta: 1 }
    case 'n':
    case 'N':
      return { type: 'newCursor' }
    case 'b':
    case 'B':
      return { type: 'newBand' }
    case 'Delete':
    case 'Backspace':
      return { type: 'removeCursor' }
    case '[':
      return { type: 'cycleCursor', dir: -1 }
    case ']':
      return { type: 'cycleCursor', dir: 1 }
    case 'g':
    case 'G':
      return { type: 'goToCursor' }
    case 't':
    case 'T':
      return { type: 'setT0' }
    case '?':
      return { type: 'help' }
  }
  return null
}

export const SHORTCUTS: [string, string][] = [
  ['← / →', 'Move the active cursor one step (0.1 s); a band moves as a whole'],
  ['Shift + ← / →', 'Ten steps'],
  ['Alt + ← / →', 'Move the end edge of the active band'],
  ['N / B', 'New cursor / new band at the centre of the view'],
  ['[ / ]', 'Previous / next cursor'],
  ['Delete', 'Remove the active cursor'],
  ['G', 'Go to the active cursor'],
  ['T', 'Move the start (t0) of the selected measurement to the active cursor'],
  ['+ / −', 'Zoom in / out'],
  ['0', 'Reset zoom'],
  ['A', 'Apply this zoom to all tabs'],
  ['Page Up / Page Down', 'Previous / next tab'],
  ['Alt + 1…9', 'Go to tab'],
  ['Alt + click / drag on a graph', 'Create a cursor / a band'],
  ['?', 'This list'],
]

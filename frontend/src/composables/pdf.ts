/** PDF of a whole tab (RF-36): every graph as an image with its legend, then the cursor table and the band statistics. jsPDF is loaded only here. */

export interface PdfTable {
  title: string
  headers: string[]
  rows: string[][]
}

export interface PdfInput {
  title: string
  subtitle: string
  images: HTMLCanvasElement[]
  tables: PdfTable[]
}

/** The built-in PDF fonts have no Greek or symbol glyphs: say them in plain letters. */
export const pdfText = (s: string) =>
  s.replace(/Δ/g, 'Delta').replace(/ƒ/g, 'f').replace(/[⚠]/g, '!').replace(/→/g, '->').replace(/[−–—]/g, '-').replace(/…/g, '...').replace(/·/g, '-').replace(/[^\x20-\xFF]/g, '?')

const A4 = { w: 297, h: 210, margin: 10 }

/** Heights (mm) for the graphs so that they share the first page; each keeps its aspect ratio. */
export function layoutImages(sizes: { w: number; h: number }[], maxHeight: number, width: number): { w: number; h: number }[] {
  if (!sizes.length) return []
  const gap = 3
  const natural = sizes.map((s) => ({ w: width, h: (width * s.h) / s.w }))
  const sum = natural.reduce((acc, s) => acc + s.h, 0)
  const f = Math.min(1, (maxHeight - gap * (sizes.length - 1)) / sum)
  return natural.map((s) => ({ w: s.w * f, h: s.h * f }))
}

export async function buildTabPdf(input: PdfInput): Promise<Blob> {
  const { jsPDF } = await import('jspdf') // heavy: only when the user asks for a PDF
  const doc = new jsPDF({ orientation: 'landscape', unit: 'mm', format: 'a4' })
  const W = A4.w - 2 * A4.margin
  let y = A4.margin
  doc.setFont('helvetica', 'bold')
  doc.setFontSize(14)
  doc.text(pdfText(input.title), A4.margin, y + 4)
  doc.setFont('helvetica', 'normal')
  doc.setFontSize(8)
  doc.setTextColor(100)
  doc.text(pdfText(input.subtitle), A4.margin, y + 10)
  doc.setTextColor(0)
  y += 14

  const sizes = layoutImages(input.images.map((c) => ({ w: c.width, h: c.height })), A4.h - A4.margin - y, W)
  input.images.forEach((c, i) => {
    doc.addImage(c.toDataURL('image/png'), 'PNG', A4.margin, y, sizes[i].w, sizes[i].h)
    y += sizes[i].h + 3
  })

  for (const t of input.tables) {
    if (!t.rows.length) continue
    doc.addPage()
    y = A4.margin
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(11)
    doc.text(pdfText(t.title), A4.margin, y + 4)
    y += 9
    const first = Math.min(70, W * 0.3)
    const rest = t.headers.length > 1 ? (W - first) / (t.headers.length - 1) : 0
    const size = rest < 16 ? 6 : 7.5
    doc.setFontSize(size)
    const rowH = size * 0.55 + 2
    const draw = (cells: string[], bold: boolean) => {
      doc.setFont('helvetica', bold ? 'bold' : 'normal')
      cells.forEach((cell, i) => {
        const x = A4.margin + (i === 0 ? 0 : first + (i - 1) * rest)
        const txt = pdfText(cell)
        if (i === 0) doc.text(txt, x, y)
        else doc.text(txt, x + rest - 1, y, { align: 'right' })
      })
      doc.setDrawColor(210)
      doc.line(A4.margin, y + 1.5, A4.margin + W, y + 1.5)
      y += rowH
    }
    draw(t.headers, true)
    for (const r of t.rows) {
      if (y > A4.h - A4.margin) {
        doc.addPage()
        y = A4.margin + 4
        draw(t.headers, true)
      }
      draw(r, false)
    }
  }
  return doc.output('blob')
}

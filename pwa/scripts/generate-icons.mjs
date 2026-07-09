// Rasterize public/favicon.svg into the PWA icon set (run: npm run icons).
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import sharp from 'sharp'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const src = path.join(root, 'public', 'favicon.svg')
const outDir = path.join(root, 'public', 'icons')
await mkdir(outDir, { recursive: true })

const targets = [
  { file: 'icon-192.png', size: 192 },
  { file: 'icon-512.png', size: 512 },
  { file: 'maskable-512.png', size: 512, pad: 64 }, // safe-zone padding
  { file: 'apple-touch-icon.png', size: 180 },
]

for (const { file, size, pad = 0 } of targets) {
  const inner = size - pad * 2
  const img = sharp(src).resize(inner, inner)
  const out = path.join(outDir, file)
  if (pad > 0) {
    await sharp({
      create: {
        width: size,
        height: size,
        channels: 4,
        background: '#f4efdd',
      },
    })
      .composite([{ input: await img.png().toBuffer(), left: pad, top: pad }])
      .png()
      .toFile(out)
  } else {
    await img.png().toFile(out)
  }
  console.log('wrote', path.relative(root, out))
}

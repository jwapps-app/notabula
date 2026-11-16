/** Shrink photos before upload. A phone camera hands over 12–48 MP files of
 * several MB — far more than a note needs — and every one is synced and kept
 * in the offline cache. Mirrors the iOS app: longest side capped at 2048 px,
 * JPEG at 0.8. */

export const MAX_IMAGE_DIMENSION = 2048
const JPEG_QUALITY = 0.8

/** Scale (w, h) to fit inside `max` on its longest side; never enlarges. */
export function fitWithin(
  width: number,
  height: number,
  max: number = MAX_IMAGE_DIMENSION,
): { width: number; height: number } {
  const scale = Math.min(1, max / Math.max(width, height))
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  }
}

function loadImage(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      URL.revokeObjectURL(url)
      resolve(img)
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('Image could not be decoded'))
    }
    img.src = url
  })
}

/**
 * Returns a downscaled copy of `file`, or `file` itself when there is nothing
 * to gain or the browser can't process it — the upload must never fail
 * because of this step.
 *
 * Left alone: images already within the cap, GIFs (resizing would drop the
 * animation), and anything the browser can't decode. PNGs stay PNG so
 * transparency and crisp screenshots survive; everything else becomes JPEG.
 * Drawing through an <img> bakes in the EXIF rotation, so the result is
 * upright without its metadata.
 */
export async function downscaleImage(
  file: File,
  max: number = MAX_IMAGE_DIMENSION,
): Promise<File> {
  if (!file.type.startsWith('image/') || file.type === 'image/gif') return file
  try {
    const img = await loadImage(file)
    const { naturalWidth: w, naturalHeight: h } = img
    if (!w || !h || Math.max(w, h) <= max) return file

    const size = fitWithin(w, h, max)
    const canvas = document.createElement('canvas')
    canvas.width = size.width
    canvas.height = size.height
    const ctx = canvas.getContext('2d')
    if (!ctx) return file
    ctx.imageSmoothingQuality = 'high'
    ctx.drawImage(img, 0, 0, size.width, size.height)

    const type = file.type === 'image/png' ? 'image/png' : 'image/jpeg'
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, type, JPEG_QUALITY),
    )
    // A blank or failed encode, or one that came out heavier than the
    // original, is worse than doing nothing.
    if (!blob || blob.type !== type || blob.size >= file.size) return file

    const ext = type === 'image/png' ? '.png' : '.jpg'
    const name = file.name.replace(/\.[^./\\]*$/, '') + ext
    return new File([blob], name || `image${ext}`, { type, lastModified: file.lastModified })
  } catch {
    return file
  }
}

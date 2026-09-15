/**
 * What happens to a pasted image before it is anything else: compressed.
 *
 * Vision billing is pixel-based - every major endpoint charges by tiles or
 * patches derived from the image's dimensions - so a screenshot at full
 * resolution costs several times what the same picture costs after its long
 * edge is capped. Compressing at paste time cuts both the visual tokens and
 * the upload, and the thumbnail preview then shows exactly what will be sent
 * and stored. The original is not kept anywhere.
 */

/** The long edge pasted images are capped to, in CSS pixels. */
export const MAX_IMAGE_SIDE = 1_568

/** An image already inside both marks is left exactly as it arrived. */
const KEEP_ORIGINAL_BYTES = 300 * 1024

/** The shape after a cap: never upscaled, proportionally scaled down. */
export function scaledSize(
  width: number,
  height: number,
  maxSide = MAX_IMAGE_SIDE,
): { width: number; height: number } {
  const longest = Math.max(width, height)
  if (longest <= maxSide) {
    return { width, height }
  }
  const scale = maxSide / longest
  return { width: Math.round(width * scale), height: Math.round(height * scale) }
}

/** Small enough, or small enough and too big: whether re-encoding earns its
 *  quality loss at all. A huge pixel size re-encodes even at few bytes, and
 *  the other way round, because both feed the same two costs. */
export function needsReencode(width: number, height: number, byteSize: number): boolean {
  return Math.max(width, height) > MAX_IMAGE_SIDE || byteSize > KEEP_ORIGINAL_BYTES
}

/**
 * One pasted image to the data URL that will be sent, previewed and stored.
 *
 * GIF is passed through untouched - the animation would be lost down to one
 * frame - and so is anything already small. Everything else lands on a white
 * backing first: a transparent PNG re-encoded as JPEG with its alpha intact
 * turns every transparent pixel black.
 */
export async function compressImage(file: Blob): Promise<string> {
  if (file.type === 'image/gif') {
    return readAsDataURL(file)
  }
  let source: ImageBitmap
  try {
    source = await createImageBitmap(file, { imageOrientation: 'from-image' })
  } catch {
    throw new Error('这张图片没法读取。')
  }
  try {
    if (!needsReencode(source.width, source.height, file.size)) {
      return readAsDataURL(file)
    }
    const { width, height } = scaledSize(source.width, source.height)
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d')
    if (context === null) {
      return readAsDataURL(file)
    }
    context.fillStyle = '#ffffff'
    context.fillRect(0, 0, width, height)
    context.drawImage(source, 0, 0, width, height)
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, 'image/jpeg', 0.85),
    )
    return blob === null ? readAsDataURL(file) : readAsDataURL(blob)
  } finally {
    source.close()
  }
}

function readAsDataURL(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error ?? new Error('这张图片没法读取。'))
    reader.readAsDataURL(blob)
  })
}

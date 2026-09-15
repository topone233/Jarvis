/**
 * A one-slot handoff for the images of a first message.
 *
 * The new-chat screen and the conversation page are different routes, and the
 * images are data URLs - too big for the history entry that carries the text,
 * and nobody else's business. In-memory on purpose: a reload in the moment
 * between the two screens loses the pictures, which is the same bargain every
 * other in-flight state here makes.
 */

let pending: string[] = []

export function setPendingImages(images: string[]): void {
  pending = images
}

export function takePendingImages(): string[] {
  const taken = pending
  pending = []
  return taken
}

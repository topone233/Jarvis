/**
 * Comparing two data-directory paths, the way the operating system does.
 *
 * The settings screen holds two strings that name the same place: the one
 * `GET /api/health` reports, and the one in the box. A byte-for-byte comparison
 * would call the directory changed every time a user typed it differently, and
 * "changed" here is not just a label - it is a button that offers to switch, and
 * a full page reload when it is pressed. A screen that reloads over a
 * difference it invented is the same defect as the one that swapped data sets by
 * accident, only quieter.
 */

/**
 * Whether two paths name the same directory, as far as a browser can tell.
 *
 * Windows ignores case, accepts either separator, and tolerates a trailing one,
 * so all three are folded away before comparing - a user who types `C:/Jarvis`
 * for the directory the API reports as `C:\Jarvis` has not chosen a different
 * place, and the screen must not say they have. Everything else a path can hide
 * - a symlink, an 8.3 short name, a mapped drive letter - is not visible from
 * here. Being wrong in that direction costs one redundant reload: the backend
 * resolves whatever it is given (`app/config.py`, `Path(...).resolve()`), so the
 * worst case is a page that reloads and shows the same data.
 */
export function sameDirectory(left: string, right: string): boolean {
  const clean = (value: string) =>
    value
      .replace(/[\\/]+/g, '/')
      .replace(/\/+$/, '')
      .toLowerCase()
  return clean(left) === clean(right)
}

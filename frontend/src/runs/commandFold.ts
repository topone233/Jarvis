/**
 * How much of a bash command the approval card shows before folding the rest
 * behind a bar. Kept apart from the component so the counting tests without a
 * DOM, the way the other pure modules in this folder do.
 *
 * The card exists to be read before it is answered, and a script of forty
 * lines would push its own buttons off the bottom of the window, where
 * nothing can scroll to them. So the command clamps to a few lines - and the
 * bar under it says what the clamp is holding back.
 */

/** Whole lines of the command that stay visible; the rest folds away. */
export const COMMAND_VISIBLE_LINES = 3

export interface CommandFold {
  /** Whole lines the clamp hides. 0 when it hides none - a command can be
   *  clamped without hiding a whole line, when one long line wraps. */
  hidden: number
  /** What the fold bar reads before it is opened. */
  label: string
}

/**
 * The command as the card shows it. Trailing newlines are dropped: a `pre`
 * renders the newline a script usually ends with as a blank last line, which
 * is one line more than the command has and one more than the count below
 * should ever see.
 */
export function commandText(command: string): string {
  return command.replace(/\n+$/, '')
}

/**
 * What the fold bar should say for a command, counted in whole lines - the
 * unit a reader of a script counts in. Trailing newlines are dropped first:
 * a script ending in one hides an empty line nowhere, and a bar promising
 * "+1 行" that opens onto nothing would be a lie.
 */
export function commandFold(command: string): CommandFold {
  const lines = commandText(command).split('\n').length
  const hidden = Math.max(0, lines - COMMAND_VISIBLE_LINES)
  return { hidden, label: hidden > 0 ? `+${hidden} 行` : '展开全部' }
}

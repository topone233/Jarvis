/**
 * Inline SVG icons. No icon library: there are about a dozen of these, they all
 * share one stroke weight, and pulling in a package to get them would mean a
 * dependency to keep pinned for something a `<path>` already does.
 *
 * Every icon draws on a 24x24 grid with `currentColor`, so size and colour come
 * from the surrounding CSS.
 */

import type { SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement> & { size?: number }

function Icon({ size = 18, children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  )
}

export function PlusIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 5v14M5 12h14" />
    </Icon>
  )
}

export function TrashIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 7h16M10 11v6M14 11v6" />
      <path d="M6 7l1 12a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-12" />
      <path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </Icon>
  )
}

export function PanelIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9.5 4v16" />
    </Icon>
  )
}

export function SendIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 19V5" />
      <path d="M5.5 11.5 12 5l6.5 6.5" />
    </Icon>
  )
}

export function StopIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="7" y="7" width="10" height="10" rx="1.5" />
    </Icon>
  )
}

export function CopyIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M6 15H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v1" />
    </Icon>
  )
}

export function CheckIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M5 12.5 10 17.5 19 7" />
    </Icon>
  )
}

export function RefreshIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M20 11a8 8 0 0 0-13.7-5.3L4 8" />
      <path d="M4 4v4h4" />
      <path d="M4 13a8 8 0 0 0 13.7 5.3L20 16" />
      <path d="M20 20v-4h-4" />
    </Icon>
  )
}

export function ThumbUpIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M7 21V10l4.2-7a1.7 1.7 0 0 1 2.8 1.8L13 10h5.3a2 2 0 0 1 2 2.3l-1.2 6.4a2 2 0 0 1-2 1.6H7" />
      <path d="M7 10H4.5a.5.5 0 0 0-.5.5v10a.5.5 0 0 0 .5.5H7" />
    </Icon>
  )
}

export function ThumbDownIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M7 3v11l4.2 7a1.7 1.7 0 0 0 2.8-1.8L13 14h5.3a2 2 0 0 0 2-2.3l-1.2-6.4a2 2 0 0 0-2-1.6H7" />
      <path d="M7 14H4.5a.5.5 0 0 1-.5-.5v-10a.5.5 0 0 1 .5-.5H7" />
    </Icon>
  )
}

export function ChevronRightIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M9.5 6 15.5 12l-6 6" />
    </Icon>
  )
}

export function ChevronDownIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 9.5 12 15.5l6-6" />
    </Icon>
  )
}

/** A magnifying glass: finding a conversation again among the pile. */
export function SearchIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16.2 16.2 4.3 4.3" />
    </Icon>
  )
}

/**
 * The conversation bubble. Gone from the expanded history rows - they are
 * words, not icons - it only survives in the collapsed rail, where every row
 * is an icon and a row with none would be a blank strip.
 */
export function MessageIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M20 12a7.5 7.5 0 0 1-7.5 7.5H8L4 22v-4.4A7.5 7.5 0 0 1 12.5 4.5 7.5 7.5 0 0 1 20 12Z" />
    </Icon>
  )
}

/** An open book: the knowledge base. */
export function BookIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 6.6C10.8 5.3 8.9 4.6 4.6 4.6v13.2c4.3 0 6.2.7 7.4 2 1.2-1.3 3.1-2 7.4-2V4.6c-4.3 0-6.2.7-7.4 2Z" />
      <path d="M12 6.6v13.2" />
    </Icon>
  )
}

/** A brain: what Jarvis keeps between conversations. */
export function BrainIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z" />
      <path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z" />
      <path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4" />
    </Icon>
  )
}

/** A gear: the one icon the whole industry agrees means settings. */
export function SettingsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1Z" />
    </Icon>
  )
}

export function EyeIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" />
      <circle cx="12" cy="12" r="3" />
    </Icon>
  )
}

/** The same eye, closed: what the button offers rather than what is on screen. */
export function EyeOffIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M10.6 6.1A8.6 8.6 0 0 1 12 6c6 0 9.5 6 9.5 6a17 17 0 0 1-2.7 3.4" />
      <path d="M6.4 7.7A17 17 0 0 0 2.5 12S6 18 12 18a8.9 8.9 0 0 0 3.9-.9" />
      <path d="M4 4l16 16" />
    </Icon>
  )
}

/** Closes a box that looks like a window. Withdrawing, never confirming. */
export function CloseIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 6l12 12M18 6L6 18" />
    </Icon>
  )
}

/** Runs a code block: a play triangle. */
export function PlayIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M8 5.5v13l10.5-6.5Z" />
    </Icon>
  )
}

/** Shows source: the angle brackets of a tag. */
export function CodeIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m8.5 7-5 5 5 5" />
      <path d="m15.5 7 5 5-5 5" />
    </Icon>
  )
}

/** Attaches an image: a paperclip. */
export function PaperclipIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M20.5 11.5 12 20a5.3 5.3 0 0 1-7.5-7.5l8.2-8.2a3.55 3.55 0 0 1 5 5l-8.1 8.2a1.8 1.8 0 0 1-2.5-2.5l7.4-7.4" />
    </Icon>
  )
}

/** Back to the newest message: a down arrow. */
export function ArrowDownIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 4v16M5.5 13.5 12 20l6.5-6.5" />
    </Icon>
  )
}

/* --- 窗口标题条的 caption 图形：跟随 Windows 的按钮形状，故无隐喻注释 --- */

export function MinusIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M5 12h14" />
    </Icon>
  )
}

export function MaximizeIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="6" y="6" width="12" height="12" rx="1.5" />
    </Icon>
  )
}

export function WindowRestoreIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="8.5" y="8.5" width="10" height="10" rx="1.5" />
      <path d="M5.5 15.5v-8a2 2 0 0 1 2-2h8" />
    </Icon>
  )
}

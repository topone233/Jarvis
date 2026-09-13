/**
 * Settings: where the data lives, which model to talk to, and what Jarvis is
 * told.
 *
 * In two shapes, and the difference is not decoration. Before anything is
 * configured this is a two-step wizard, because the order is a data dependency
 * rather than a convention: model profiles are rows inside the data directory's
 * database, so until a directory has been chosen there is nowhere to put one and
 * every other route answers 409. That is what makes the first run go 1 then 2 -
 * and it is also why the tabs are not offered there: two of the three would open
 * onto an error.
 *
 * Afterwards the same screen is a list on the left and one panel on the right.
 * The three have nothing to do with each other - a directory, a set of profiles,
 * three prompts - and stacking them made a page that grew a screenful longer
 * every time a setting was added. The tab is in the URL, so a refresh comes back
 * to the same one and the address can be linked to.
 *
 * The directory panel is written here rather than in `settings/`, because the
 * directory is the shell's own state: it is what decides whether the app has
 * been configured at all, and the health check that fills the box runs on the
 * page's first render either way.
 */

import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'

import { setupDataDirectory } from '../api/endpoints'
import { ApiError, checkHealth } from '../api/client'
import { sameDirectory } from '../api/paths'
import { CloseIcon } from '../components/icons'
import { useConfirm } from '../hooks/useConfirm'
import { ModelProfilesPanel } from './settings/ModelProfilesPanel'
import { PromptsPanel } from './settings/PromptsPanel'

const TABS = ['directory', 'model', 'prompts'] as const

type Tab = (typeof TABS)[number]

const TAB_LABELS: Record<Tab, string> = {
  directory: '数据目录',
  model: '模型',
  prompts: '提示词',
}

/** Anything unrecognised means the first tab, so an edited URL still lands somewhere. */
function tabFrom(value: string | null): Tab {
  return TABS.find((tab) => tab === value) ?? 'directory'
}

export function SetupPage({
  configured,
  onClose,
  onConfigured,
}: {
  /** False on the first run, when no data directory has been chosen yet. */
  configured: boolean
  /**
   * Leaving the screen, for the close button in the corner. Absent on the first
   * run, where the button is absent too: there is nowhere else to be yet, and
   * offering a way out would strand someone with no data directory. Where it
   * goes is the caller's business - only the shell knows what came before.
   */
  onClose?: () => void
  onConfigured(): void
}) {
  const navigate = useNavigate()
  const confirm = useConfirm()
  const [searchParams] = useSearchParams()

  // Wizard only.
  const [step, setStep] = useState<1 | 2>(1)

  const [dataDir, setDataDir] = useState('')
  const [current, setCurrent] = useState<string | null>(null)
  // Set when a directory had been chosen and could not be opened - deleted, or
  // its drive is not mounted. Without it this screen looks exactly like a first
  // run, and the path in the box has no explanation for why it is not working.
  const [dirError, setDirError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Which directory is in use right now. The field started empty once, so a
  // second visit to this page and one click could quietly point the whole app at
  // somewhere else - and the conversations are inside that somewhere else, so
  // they looked deleted.
  useEffect(() => {
    void checkHealth()
      .then((health) => {
        setCurrent(health.data_directory)
        setDirError(health.data_directory_error)
        const directory = health.data_directory
        if (directory !== null) {
          // Fills the box only while it is untouched, so a slow health check
          // cannot overwrite something the user has already typed.
          setDataDir((typed) => (typed === '' ? directory : typed))
        }
      })
      .catch(() => undefined)
  }, [])

  async function applyDirectory() {
    const chosen = dataDir.trim()
    if (chosen === '') {
      setError('请填写一个数据目录。')
      return
    }
    if (configured) {
      const confirmed = await confirm.ask({
        title: '换成另一个数据目录？',
        body: '对话、记忆、知识库和模型配置都存在这个目录里，换一个看到的就是另一套。',
        confirmLabel: '切换目录',
      })
      if (!confirmed) {
        return
      }
    }
    setBusy(true)
    setError(null)
    try {
      await setupDataDirectory(chosen)
      if (current !== null && !sameDirectory(chosen, current)) {
        // Conversations, memories, knowledge and model profiles all live inside
        // this directory, so pointing at a different one changes every list on
        // screen at once. Starting over is the honest way to show that; the
        // alternative is a sidebar full of rows that no longer resolve.
        window.location.assign('/')
        return
      }
      setStep(2)
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setBusy(false)
    }
  }

  const unchanged = current !== null && sameDirectory(dataDir.trim(), current)
  const tab = tabFrom(searchParams.get('tab'))

  const directorySection = (
    <section className="setup-section">
      {/* No heading here: the field below is labelled 数据目录, and it has to
          stay - it is what names the input and focuses it when clicked. */}
      <div className="field">
        <label htmlFor="data-directory">数据目录</label>
        <input
          id="data-directory"
          value={dataDir}
          placeholder="D:\JarvisData"
          onChange={(event) => setDataDir(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              void applyDirectory()
            }
          }}
        />
        <span className="hint">
          对话、记忆、知识库和模型配置都存在这里。目录要已经存在——Jarvis
          不会替你创建，打错的路径会直接报错，而不是悄悄变成一个空目录。
        </span>
        {current !== null && (
          <span className="current-dir">
            {/* "Current" is only true while it works. When it does not, the path
                is still the one to show - it is the one that went missing. */}
            {dirError === null ? '当前使用的是 ' : '上次使用的是 '}
            <code>{current}</code>
            {configured ? '。换成别的目录，看到的就是另一套数据。' : '。'}
          </span>
        )}
        {dirError !== null && <span className="field-error">{dirError}</span>}
      </div>
      <div className="setup-actions">
        <span className="spacer" />
        <button
          type="button"
          className="button button-primary"
          disabled={busy || (configured && unchanged)}
          onClick={() => void applyDirectory()}
        >
          {configured ? '切换目录' : '下一步'}
        </button>
      </div>
      {error !== null && <div className="form-error">{error}</div>}
    </section>
  )

  const wizardStepTwo = (
    <ModelProfilesPanel
      configured={false}
      onBack={() => {
        setError(null)
        setStep(1)
      }}
      onConfigured={() => {
        onConfigured()
        navigate('/')
      }}
    />
  )

  return (
    <div className="setup">
      <div className={`setup-card${configured ? ' is-manager' : ''}`}>
        {onClose !== undefined && (
          // The card is a route, so it is not a window that can be dismissed -
          // but it is shaped like one, and the sidebar is a long way to go to
          // say "done here".
          <button
            type="button"
            className="icon-button setup-close"
            aria-label="关闭设置"
            title="关闭"
            onClick={onClose}
          >
            <CloseIcon size={16} />
          </button>
        )}
        <h1 className="setup-title">{configured ? '设置' : '配置 Jarvis'}</h1>
        <p className="setup-subtitle">
          {configured
            ? '数据放在哪，用哪个模型，跟它说什么，都在这里改。'
            : '两步就好，之后随时可以在左侧「设置」里改。'}
        </p>

        {!configured && (
          <div className="setup-steps">
            <span className={`setup-step${step === 1 ? ' is-active' : ' is-done'}`}>
              <span className="index">{step === 1 ? '1' : '✓'}</span>
              数据目录
            </span>
            <span className={`setup-step${step === 2 ? ' is-active' : ''}`}>
              <span className="index">2</span>
              模型
            </span>
          </div>
        )}

        {!configured ? (
          step === 1 ? (
            directorySection
          ) : (
            wizardStepTwo
          )
        ) : (
          <div className="setup-layout">
            {/* Links rather than buttons: the tab is in the URL, so these are
                real navigation - which also gets middle-click and ctrl-click for
                free, and puts the tab in the browser's history. */}
            <nav className="setup-tabs" aria-label="设置分类">
              {TABS.map((name) => (
                <Link
                  key={name}
                  to={`/setup?tab=${name}`}
                  className={`setup-tab${name === tab ? ' is-active' : ''}`}
                  aria-current={name === tab ? 'page' : undefined}
                >
                  {TAB_LABELS[name]}
                </Link>
              ))}
            </nav>
            <div className="setup-panel">
              {tab === 'directory' && directorySection}
              {tab === 'model' && <ModelProfilesPanel configured />}
              {tab === 'prompts' && <PromptsPanel />}
            </div>
          </div>
        )}
      </div>
      {confirm.dialog}
    </div>
  )
}

function describe(cause: unknown): string {
  if (cause instanceof ApiError || cause instanceof Error) {
    return cause.message
  }
  return '出了点问题，请再试一次。'
}

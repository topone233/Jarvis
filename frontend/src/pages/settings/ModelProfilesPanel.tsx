/**
 * The model tab: the profiles, and the form that edits one of them.
 *
 * Also the second step of the first-run wizard. The order there is a data
 * dependency, not a layout choice - profiles are rows inside the data
 * directory's database - so the wizard renders this same panel with
 * `configured` false, and the only differences are the footer and the fact that
 * the form stays open after a save.
 */

import { useEffect, useState } from 'react'

import {
  createModelProfile,
  deleteModelProfile,
  listModelProfiles,
  testModelProfile,
  updateModelProfile,
} from '../../api/endpoints'
import { ApiError } from '../../api/client'
import {
  compactAt,
  createPayload,
  draftFrom,
  effectiveDefault,
  emptyDraft,
  LIMITS,
  profileValues,
  updatePayload,
  type ProfileDraft,
} from '../../api/profiles'
import type { ModelProfile } from '../../api/types'
import { EyeIcon, EyeOffIcon } from '../../components/icons'
import { useConfirm } from '../../hooks/useConfirm'
import { useToast } from '../../hooks/useToast'

export function ModelProfilesPanel({
  configured,
  onBack,
  onConfigured,
}: {
  /** False during the first run, when there is no settings page to manage yet. */
  configured: boolean
  /** Wizard only: back to the data directory step. */
  onBack?: () => void
  /** Wizard only: the way into the app, offered once a save has been tested. */
  onConfigured?: () => void
}) {
  const confirm = useConfirm()
  const toast = useToast()

  const [profiles, setProfiles] = useState<ModelProfile[]>([])
  // The wizard has the form on screen from the start. The settings page opens it
  // on request, either for a new profile or for the one being edited, so `null`
  // is a state it actually uses.
  const [draft, setDraft] = useState<ProfileDraft | null>(configured ? null : emptyDraft())

  const [busy, setBusy] = useState(false)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [testNote, setTestNote] = useState<string | null>(null)

  // Only asked for once there is a directory: before that the route answers 409,
  // and "there are no profiles" and "there is nowhere to keep them" would look
  // the same on screen.
  useEffect(() => {
    if (!configured) {
      return
    }
    void listModelProfiles()
      .then(setProfiles)
      .catch((cause: unknown) => setError(describe(cause)))
  }, [configured])

  /** Re-reads the list after a change. */
  async function reloadProfiles() {
    try {
      setProfiles(await listModelProfiles())
    } catch {
      // Not worth failing the screen over: whatever change prompted this has
      // already been made, and the list is read again the next time the page
      // opens.
    }
  }

  async function saveProfile() {
    if (draft === null) {
      return
    }
    if (draft.name.trim() === '' || draft.baseUrl.trim() === '' || draft.chatModel.trim() === '') {
      setError('名称、接口地址和模型名都要填。')
      return
    }
    const values = profileValues(draft)
    if (typeof values === 'string') {
      setError(values)
      return
    }
    setBusy(true)
    setError(null)
    setTestNote(null)
    try {
      const profile =
        draft.id === null
          ? await createModelProfile(createPayload(draft, profiles.length === 0, values))
          : await updateModelProfile(draft.id, updatePayload(draft, values))
      if (configured) {
        // The list behind the form is now the truth about what exists, and the
        // test result belongs next to it rather than inside a form the user is
        // finished with.
        setDraft(null)
      } else {
        // Staying on the form means a second 保存 has to edit this profile
        // instead of adding another one, and the key it just stored is not
        // retyped - so the box is emptied and the id remembered.
        setDraft({ ...draft, id: profile.id, apiKey: '' })
      }
      await reloadProfiles()
      // The entry into the app is deliberately left to the button below, after
      // the test has had its say. Leaving the screen here instead meant the test
      // result was thrown away unseen - and with it the only moment where a
      // wrong address or a bad key could still be corrected on the spot.
      setTesting(true)
      try {
        const result = await testModelProfile(profile.id)
        setTestNote(`连接正常，这个接口提供 ${result.models.length} 个模型。`)
      } catch (cause) {
        setTestNote(`已经保存，但连接测试没有通过：${describe(cause)}`)
      } finally {
        setTesting(false)
      }
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setBusy(false)
    }
  }

  async function makeDefault(profile: ModelProfile) {
    setBusy(true)
    setError(null)
    try {
      await updateModelProfile(profile.id, { is_default: true })
      await reloadProfiles()
      // The badge moves to another row, which is easy to miss in a list.
      toast.show('已设为默认')
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setBusy(false)
    }
  }

  async function removeProfile(profile: ModelProfile) {
    const confirmed = await confirm.ask({
      title: `删掉模型配置「${profile.name}」？`,
      body: profile.is_default ? '它是当前默认，删掉之后要重新指定一条。' : undefined,
      confirmLabel: '删除',
      danger: true,
    })
    if (!confirmed) {
      return
    }
    setBusy(true)
    setError(null)
    try {
      await deleteModelProfile(profile.id)
      if (draft?.id === profile.id) {
        setDraft(null)
      }
      await reloadProfiles()
      toast.show('已删除')
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setBusy(false)
    }
  }

  function openEditor(profile: ModelProfile) {
    setDraft(draftFrom(profile))
    setError(null)
    setTestNote(null)
  }

  function startNewProfile() {
    setDraft(emptyDraft())
    setError(null)
    setTestNote(null)
  }

  function closeEditor() {
    setDraft(null)
    setError(null)
    setTestNote(null)
  }

  const patchDraft = (patch: Partial<ProfileDraft>) =>
    setDraft((open) => (open === null ? open : { ...open, ...patch }))

  const storedKey =
    draft !== null &&
    draft.id !== null &&
    profiles.some((item) => item.id === draft.id && item.has_api_key)
  const noDefault = profiles.length > 0 && !profiles.some((item) => item.is_default)
  const fallback = configured ? effectiveDefault(profiles) : null

  return (
    <section className="setup-section">
      {draft !== null && (
        <ProfileForm
          // Keyed by profile: whether the key is being shown belongs to the form
          // on screen, so opening another profile - or a blank one - hides it
          // again rather than carrying the last choice over.
          key={draft.id ?? 'new'}
          draft={draft}
          storedKey={storedKey}
          onPatch={patchDraft}
        />
      )}
      {configured && draft !== null && (
        <div className="setup-actions">
          <button
            type="button"
            className="button button-ghost"
            disabled={busy}
            onClick={closeEditor}
          >
            取消
          </button>
          <span className="spacer" />
          <button
            type="button"
            className="button button-primary"
            disabled={busy || testing}
            onClick={() => void saveProfile()}
          >
            保存
          </button>
        </div>
      )}
      {configured && draft === null && profiles.length === 0 && (
        <p className="setup-empty">还没有模型配置。</p>
      )}
      {configured && profiles.length > 0 && (
        <ul className="profile-list">
          {profiles.map((profile) => (
            <li
              key={profile.id}
              className={`profile-row${profile.id === draft?.id ? ' is-editing' : ''}`}
            >
              <div className="profile-head">
                <span className="profile-name">{profile.name}</span>
                {profile.is_default && <span className="badge">默认</span>}
              </div>
              <div className="profile-meta">
                {profile.base_url} · {profile.chat_model} ·{' '}
                {profile.has_api_key ? '密钥已保存' : '没有密钥'}
              </div>
              <div className="profile-meta">
                {profile.context_window} tokens 上下文 · 输出预留 {profile.output_token_reserve} ·{' '}
                {profile.max_tokens === null ? '不限生成长度' : `上限 ${profile.max_tokens}`} ·
                压缩阈值 {profile.compact_percent}%
              </div>
              <div className="profile-actions">
                <button
                  type="button"
                  className="button button-ghost button-small"
                  disabled={busy}
                  onClick={() => openEditor(profile)}
                >
                  编辑
                </button>
                {!profile.is_default && (
                  <button
                    type="button"
                    className="button button-ghost button-small"
                    disabled={busy}
                    onClick={() => void makeDefault(profile)}
                  >
                    设为默认
                  </button>
                )}
                <button
                  type="button"
                  className="button button-ghost button-small"
                  disabled={busy}
                  onClick={() => void removeProfile(profile)}
                >
                  删除
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {fallback !== null && noDefault && (
        // Deleting the default leaves the list without one, and that is allowed.
        // Saying which profile is used in its place is the difference between a
        // missing badge and an unexplained one.
        <p className="setup-hint">还没有指定默认配置，新对话会用最早创建的「{fallback.name}」。</p>
      )}
      {configured && draft === null && (
        <div className="setup-actions">
          <span className="spacer" />
          <button
            type="button"
            className="button button-ghost"
            disabled={busy}
            onClick={startNewProfile}
          >
            添加配置
          </button>
        </div>
      )}
      {!configured && (
        <div className="setup-actions">
          <button type="button" className="button button-ghost" disabled={busy} onClick={onBack}>
            上一步
          </button>
          <span className="spacer" />
          <button
            type="button"
            className="button button-primary"
            disabled={busy}
            onClick={() => void saveProfile()}
          >
            保存
          </button>
          {testNote !== null && !testing && (
            <button type="button" className="button button-primary" onClick={onConfigured}>
              开始使用
            </button>
          )}
        </div>
      )}
      {error !== null && <div className="form-error">{error}</div>}
      {testing && <div className="form-ok">正在测试连接…</div>}
      {testNote !== null && !testing && <div className="form-ok">{testNote}</div>}
      {confirm.dialog}
    </section>
  )
}

/** The boxes, shared by the wizard and the settings page. */
function ProfileForm({
  draft,
  storedKey,
  onPatch,
}: {
  draft: ProfileDraft
  /** Whether a key is already in the credential manager for this profile. */
  storedKey: boolean
  onPatch(patch: Partial<ProfileDraft>): void
}) {
  // The eye reveals what is being typed, and nothing more. A key that is already
  // saved is not renderable here at all - the API never returns it, only a
  // `has_api_key` flag - so this switch has nothing to uncover for it, and the
  // placeholder stays a sentence rather than a secret.
  const [revealed, setRevealed] = useState(false)
  const threshold = compactAt(draft)

  return (
    <>
      <div className="field">
        <label htmlFor="profile-name">配置名称</label>
        <input
          id="profile-name"
          value={draft.name}
          placeholder="本地模型"
          onChange={(event) => onPatch({ name: event.target.value })}
        />
      </div>
      <div className="field">
        <label htmlFor="profile-base-url">接口地址</label>
        <input
          id="profile-base-url"
          value={draft.baseUrl}
          placeholder="https://api.deepseek.com/v1"
          onChange={(event) => onPatch({ baseUrl: event.target.value })}
        />
        <span className="hint">OpenAI 兼容的接口根地址，通常以 /v1 结尾。</span>
      </div>
      <div className="field">
        <label htmlFor="profile-chat-model">模型名</label>
        <input
          id="profile-chat-model"
          value={draft.chatModel}
          placeholder="deepseek-chat"
          onChange={(event) => onPatch({ chatModel: event.target.value })}
        />
      </div>
      <div className="field">
        <label htmlFor="profile-api-key">API Key</label>
        <div className="field-control">
          <input
            id="profile-api-key"
            type={revealed ? 'text' : 'password'}
            value={draft.apiKey}
            placeholder={storedKey ? '已保存，留空表示不改动' : '本地模型可以留空'}
            onChange={(event) => onPatch({ apiKey: event.target.value })}
          />
          <button
            type="button"
            className={`icon-button field-action${revealed ? ' is-on' : ''}`}
            // The label describes the button, so a screen reader hears what
            // pressing it does; `aria-pressed` says which way it is set now.
            aria-label={revealed ? '隐藏密钥' : '显示密钥'}
            aria-pressed={revealed}
            title={revealed ? '隐藏' : '显示'}
            onClick={() => setRevealed((shown) => !shown)}
          >
            {revealed ? <EyeOffIcon size={16} /> : <EyeIcon size={16} />}
          </button>
        </div>
        <span className="hint">
          保存在系统的凭据管理器里，不写进数据库。
          {storedKey && ' 服务端从不回传密钥，所以这里不会显示出已保存的那一个。'}
        </span>
      </div>

      <h3 className="form-group-title">调用参数</h3>
      <div className="field-row">
        <div className="field">
          <label htmlFor="profile-context-window">上下文大小</label>
          <input
            id="profile-context-window"
            inputMode="numeric"
            value={draft.contextWindow}
            placeholder={`${LIMITS.contextWindow.min}`}
            onChange={(event) => onPatch({ contextWindow: event.target.value })}
          />
          <span className="hint">这个模型一次能装下的 token 数。</span>
        </div>
        <div className="field">
          <label htmlFor="profile-output-reserve">输出预留</label>
          <input
            id="profile-output-reserve"
            inputMode="numeric"
            value={draft.outputTokenReserve}
            placeholder={`${LIMITS.outputTokenReserve.min}`}
            onChange={(event) => onPatch({ outputTokenReserve: event.target.value })}
          />
          <span className="hint">给回答留出的额度，剩下的才装输入。</span>
        </div>
      </div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="profile-max-tokens">生成长度上限</label>
          <input
            id="profile-max-tokens"
            inputMode="numeric"
            value={draft.maxTokens}
            placeholder="留空 = 不限制"
            onChange={(event) => onPatch({ maxTokens: event.target.value })}
          />
          <span className="hint">留空就不发这个字段，由服务商自己决定。</span>
        </div>
        <div className="field">
          <label htmlFor="profile-compact-percent">压缩阈值</label>
          <input
            id="profile-compact-percent"
            inputMode="numeric"
            value={draft.compactPercent}
            placeholder={`${LIMITS.compactPercent.min}`}
            onChange={(event) => onPatch({ compactPercent: event.target.value })}
          />
          <span className="hint">
            {threshold === null
              ? '占输入预算的百分比：用掉这么多之后，先把早先的对话整理成摘要。'
              : `占输入预算的百分比：用掉约 ${threshold} tokens 之后，先把早先的对话整理成摘要。`}
          </span>
        </div>
      </div>

      <h3 className="form-group-title">思考</h3>
      <p className="hint">
        输入框里的「思考」是个四档的滑杆，拨给你这套接口看的：关那一档发下面这段， low / high / max
        三档发的是 <code>reasoning_effort</code> 加上档位名 ——
        那个字段名是本程序自己定的，不需要在这里配置。
        原来「思考开」那段已经不再发送了；填过的内容仍然原样留着，不会被清掉。
      </p>
      <div className="field">
        <label htmlFor="profile-thinking-off">思考关时要加的字段</label>
        <textarea
          id="profile-thinking-off"
          rows={3}
          value={draft.thinkingOff}
          placeholder={'{"enable_thinking": false}'}
          onChange={(event) => onPatch({ thinkingOff: event.target.value })}
        />
        <span className="hint">
          各家写法不同：<code>enable_thinking</code>、<code>reasoning_effort</code>、
          <code>thinking.type</code> 都有人用，接口不认识这些键就会报错，所以按服务商的文档写。
        </span>
      </div>
    </>
  )
}

function describe(cause: unknown): string {
  if (cause instanceof ApiError || cause instanceof Error) {
    return cause.message
  }
  return '出了点问题，请再试一次。'
}

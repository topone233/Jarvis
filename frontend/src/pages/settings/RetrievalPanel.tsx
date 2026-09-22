/**
 * The retrieval-model tab: one card for the embedding model, one for the
 * reranker, each configured apart from the chat profiles.
 *
 * The two cards are independent on purpose and the server enforces it - a PUT
 * that names one kind leaves the other untouched - so a card saves alone and
 * carries no state about its sibling. Nothing here blocks the app either: an
 * unconfigured embedding model means keyword-only knowledge search and no
 * memory ranking, both of which degrade quietly.
 */

import { useEffect, useState } from 'react'

import {
  clearPatch,
  draftFromSetting,
  fetchRetrievalSettings,
  savePatch,
  saveRetrievalSettings,
  testEmbedding,
  testRerank,
  thresholdsDraftFrom,
  thresholdsError,
  thresholdsPayload,
  THRESHOLDS_DEFAULT_PATCH,
  RETRIEVAL_THRESHOLDS,
  type RetrievalDraft,
  type RetrievalKind,
  type ThresholdDraft,
  type ThresholdKey,
} from '../../api/retrieval'
import { ApiError } from '../../api/client'
import type { RetrievalModelSetting, RetrievalSettings } from '../../api/types'
import { EyeIcon, EyeOffIcon } from '../../components/icons'
import { useConfirm } from '../../hooks/useConfirm'

const CARD_COPY: Record<
  RetrievalKind,
  { title: string; description: string; modelPlaceholder: string }
> = {
  embedding: {
    title: '嵌入模型',
    description:
      '知识库语义检索和记忆相关性排序用的向量模型。配置后，新导入的文档会建立语义索引，检索按 向量 + 关键词 混合进行。',
    modelPlaceholder: 'text-embedding-3-small、bge-m3 这类',
  },
  rerank: {
    title: 'Rerank 模型',
    description:
      '可选的精排阶段：混合检索先取 20 条候选，交给 rerank 模型按相关性重排后取前几条。未配置或调用失败时，直接用混合排序的结果。',
    modelPlaceholder: 'Cohere 兼容 /rerank 接口的模型名',
  },
}

export function RetrievalPanel() {
  const [settings, setSettings] = useState<RetrievalSettings | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Asked once when the tab opens; the cards keep it fresh through onSaved,
  // whose responses carry both kinds, so nothing re-reads behind their backs.
  useEffect(() => {
    void fetchRetrievalSettings()
      .then(setSettings)
      .catch((cause: unknown) => setError(describe(cause)))
  }, [])

  if (settings === null) {
    return (
      <section className="setup-section">
        {error !== null && <div className="form-error">{error}</div>}
      </section>
    )
  }

  return (
    <section className="setup-section">
      <RetrievalCard kind="embedding" setting={settings.embedding} onSaved={setSettings} />
      <RetrievalCard kind="rerank" setting={settings.rerank} onSaved={setSettings} />
      <ThresholdsCard settings={settings} onSaved={setSettings} />
    </section>
  )
}

/**
 * The third card: the relevance floors a retrieval pass applies. Draft-based
 * like the tool-calls tab - 保存 parses the three inputs, 恢复默认 sends
 * nulls immediately and throws away what was typed. The floors are read by
 * the next search, not the one already going.
 */
function ThresholdsCard({
  settings,
  onSaved,
}: {
  settings: RetrievalSettings
  /** Hands the server's response back to the panel, all cards included. */
  onSaved(settings: RetrievalSettings): void
}) {
  const [draft, setDraft] = useState<ThresholdDraft>(() => thresholdsDraftFrom(settings))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const stored = thresholdsDraftFrom(settings)
  const changed =
    JSON.stringify(serializeThresholds(draft)) !== JSON.stringify(serializeThresholds(stored))
  const isDefault =
    settings.thresholds.memory_floor.is_default &&
    settings.thresholds.semantic_floor.is_default &&
    settings.thresholds.rerank_floor.is_default

  const edit = (key: ThresholdKey, text: string) => {
    setDraft({ ...draft, [key]: text })
    setNote(null)
  }

  const apply = (next: RetrievalSettings, message: string) => {
    onSaved(next)
    setDraft(thresholdsDraftFrom(next))
    setNote(message)
  }

  const save = async () => {
    const problem = thresholdsError(draft)
    if (problem !== null) {
      setNote(null)
      setError(problem)
      return
    }
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      apply(await saveRetrievalSettings(thresholdsPayload(draft, settings)), '已保存')
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setBusy(false)
    }
  }

  const restoreDefault = async () => {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      apply(await saveRetrievalSettings(THRESHOLDS_DEFAULT_PATCH), '已恢复默认。')
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <h3 className="form-group-title">检索阈值</h3>
      <p className="hint">
        检索结果的相关度门槛，低于门槛的命中不进记忆注入和引用。合适的值跟嵌入 / rerank
        模型强相关：用知识库页的「试试搜索」观察命中分数，无关命中的分数普遍高于某个值时，把对应门槛抬到它之上。
      </p>
      {(Object.keys(RETRIEVAL_THRESHOLDS) as ThresholdKey[]).map((key) => (
        <div className="field" key={key}>
          <label htmlFor={`threshold-${key}`}>{RETRIEVAL_THRESHOLDS[key].label}</label>
          <input
            id={`threshold-${key}`}
            type="number"
            step={0.05}
            min={RETRIEVAL_THRESHOLDS[key].min}
            max={RETRIEVAL_THRESHOLDS[key].max}
            value={draft[key]}
            placeholder={`默认 ${settings.thresholds[key].default}`}
            onChange={(event) => edit(key, event.target.value)}
          />
          <span className="hint">{THRESHOLD_HINTS[key]}</span>
        </div>
      ))}
      <div className="setup-actions">
        {isDefault ? (
          <span className="badge badge-quiet">默认</span>
        ) : (
          <>
            <span className="badge">已改过</span>
            <button
              type="button"
              className="button button-ghost button-small"
              disabled={busy}
              title="丢掉这里的修改，回到内置的值"
              onClick={() => void restoreDefault()}
            >
              恢复默认
            </button>
          </>
        )}
        <span className="spacer" />
        <button
          type="button"
          className="button button-primary"
          disabled={busy || !changed}
          onClick={() => void save()}
        >
          保存
        </button>
      </div>
      {error !== null && <div className="form-error">{error}</div>}
      {note !== null && <div className="form-ok">{note}</div>}
    </>
  )
}

const THRESHOLD_HINTS: Record<ThresholdKey, string> = {
  memory_floor: '注入记忆前，记忆内容与这次提问的相似度下限。无关记忆常被召回时调高。',
  semantic_floor: '知识库语义命中的相似度下限，未配置嵌入模型时不生效。',
  rerank_floor: '重排结果的相关性下限，未配置 Rerank 模型时不生效；全部低于门槛就宁可不引用。',
}

/** One kind's boxes, test button, save button, and clear action. */
function RetrievalCard({
  kind,
  setting,
  onSaved,
}: {
  kind: RetrievalKind
  /** What is stored right now, or null when this kind is not configured. */
  setting: RetrievalModelSetting | null
  /** Hands the server's response back to the panel, both cards included. */
  onSaved(settings: RetrievalSettings): void
}) {
  const confirm = useConfirm()
  const copy = CARD_COPY[kind]
  const [draft, setDraft] = useState<RetrievalDraft>(() => draftFromSetting(setting))
  const [busy, setBusy] = useState(false)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  // The eye reveals what is being typed. A saved key is not renderable here at
  // all - the API never returns it, only the `has_api_key` flag - so the
  // placeholder is a sentence, not a secret.
  const [revealed, setRevealed] = useState(false)

  const patchDraft = (patch: Partial<RetrievalDraft>) => setDraft((open) => ({ ...open, ...patch }))

  async function save() {
    const patch = savePatch(kind, draft)
    if (typeof patch === 'string') {
      setError(patch)
      return
    }
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      const next = await saveRetrievalSettings(patch)
      onSaved(next)
      // The stored state is now the boxes' truth: a saved key is not shown, so
      // the box goes back to empty, and the placeholder switches to 已保存.
      setDraft(draftFromSetting(next[kind]))
      setNote('已保存')
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setBusy(false)
    }
  }

  async function test() {
    setBusy(true)
    setTesting(true)
    setError(null)
    setNote(null)
    try {
      const result = kind === 'embedding' ? await testEmbedding(draft) : await testRerank(draft)
      setNote(
        kind === 'embedding' ? `连接正常，向量维度 ${result.dimensions ?? '?'}。` : '连接正常。',
      )
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setBusy(false)
      setTesting(false)
    }
  }

  async function clear() {
    const confirmed = await confirm.ask({
      title: `清除${copy.title}配置？`,
      body: '接口地址、模型名和已保存的 API Key 都会被删除，检索退回到没有这一层的状态。',
      confirmLabel: '清除',
      danger: true,
    })
    if (!confirmed) {
      return
    }
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      const next = await saveRetrievalSettings(clearPatch(kind))
      onSaved(next)
      setDraft(draftFromSetting(null))
      setNote('已清除')
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <h3 className="form-group-title">{copy.title}</h3>
      <p className="hint">{copy.description}</p>
      <div className="field">
        <label htmlFor={`${kind}-base-url`}>接口地址</label>
        <input
          id={`${kind}-base-url`}
          value={draft.baseUrl}
          placeholder="https://api.siliconflow.cn/v1"
          onChange={(event) => patchDraft({ baseUrl: event.target.value })}
        />
        <span className="hint">OpenAI 兼容的接口根地址，通常以 /v1 结尾。</span>
      </div>
      <div className="field">
        <label htmlFor={`${kind}-model`}>模型名</label>
        <input
          id={`${kind}-model`}
          value={draft.model}
          placeholder={copy.modelPlaceholder}
          onChange={(event) => patchDraft({ model: event.target.value })}
        />
      </div>
      <div className="field">
        <label htmlFor={`${kind}-api-key`}>API Key</label>
        <div className="field-control">
          <input
            id={`${kind}-api-key`}
            type={revealed ? 'text' : 'password'}
            value={draft.apiKey}
            placeholder={setting?.has_api_key ? '已保存，留空表示不改动' : '本地服务可以留空'}
            onChange={(event) => patchDraft({ apiKey: event.target.value })}
          />
          <button
            type="button"
            className={`icon-button field-action${revealed ? ' is-on' : ''}`}
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
          {setting?.has_api_key && ' 服务端从不回传密钥，所以这里不会显示出已保存的那一个。'}
        </span>
      </div>
      <div className="setup-actions">
        {setting !== null && (
          <button
            type="button"
            className="button button-ghost"
            disabled={busy}
            onClick={() => void clear()}
          >
            清除配置
          </button>
        )}
        <span className="spacer" />
        <button
          type="button"
          className="button button-ghost"
          disabled={busy}
          onClick={() => void test()}
        >
          测试
        </button>
        <button
          type="button"
          className="button button-primary"
          disabled={busy}
          onClick={() => void save()}
        >
          保存
        </button>
      </div>
      {error !== null && <div className="form-error">{error}</div>}
      {testing && <div className="form-ok">正在测试连接…</div>}
      {note !== null && !testing && <div className="form-ok">{note}</div>}
    </>
  )
}

function describe(cause: unknown): string {
  if (cause instanceof ApiError || cause instanceof Error) {
    return cause.message
  }
  return '出了点问题，请再试一次。'
}

/** The draft as the store holds it - trimmed - so `changed` can compare. */
function serializeThresholds(draft: ThresholdDraft): string[] {
  return (Object.keys(RETRIEVAL_THRESHOLDS) as ThresholdKey[]).map((key) => draft[key].trim())
}

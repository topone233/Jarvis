/**
 * The skills tab: what is installed, what is on, and the way in.
 *
 * Import names a local folder; the backend validates its SKILL.md and copies
 * the whole tree into the data directory, so the original can be moved or
 * deleted without anything here breaking. A card's switch applies the moment
 * it is flipped - one boolean has nothing to draft and no save button - and a
 * broken folder still shows as a card with its error instead of a switch,
 * because a hand-copied folder with a typo is only fixable if it is visible.
 *
 * Skills trigger two ways once installed: typing `/技能名` in a conversation,
 * or the model calling the skill tool on its own judgement. Both are the
 * backend's business; this tab's whole job is the inventory.
 */

import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '../../api/client'
import { deleteSkill, importSkill, listSkills, setSkillEnabled } from '../../api/skills'
import type { SkillInfo } from '../../api/types'
import { TrashIcon } from '../../components/icons'
import { useConfirm } from '../../hooks/useConfirm'
import { useToast } from '../../hooks/useToast'

export function SkillsPanel() {
  const confirm = useConfirm()
  const toast = useToast()
  const [skills, setSkills] = useState<SkillInfo[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [path, setPath] = useState('')
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(() => {
    return listSkills()
      .then((installed) => {
        setSkills(installed)
        setLoadError(null)
      })
      .catch((cause: unknown) => setLoadError(describe(cause)))
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  async function doImport() {
    setImporting(true)
    setError(null)
    try {
      await importSkill(path)
      // The box clears so pasting a second path does not have to fight the
      // first one's leftovers; the card appearing is the result itself.
      setPath('')
      await reload()
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setImporting(false)
    }
  }

  async function toggle(skill: SkillInfo, enabled: boolean) {
    setError(null)
    try {
      const next = await setSkillEnabled(skill.name, enabled)
      setSkills((current) => current?.map((s) => (s.name === next.name ? next : s)) ?? current)
    } catch (cause) {
      setError(describe(cause))
    }
  }

  async function remove(skill: SkillInfo) {
    const confirmed = await confirm.ask({
      title: `删除技能 /${skill.name}？`,
      body: '技能文件夹会从数据目录里整个删掉，对话里的 /技能名 也不再生效。原文件夹不受影响。',
      confirmLabel: '删除',
      danger: true,
    })
    if (!confirmed) {
      return
    }
    try {
      await deleteSkill(skill.name)
      await reload()
      toast.show('已删除')
    } catch (cause) {
      setError(describe(cause))
    }
  }

  return (
    <section className="setup-section">
      <div className="field">
        <label htmlFor="skill-path">导入本地 skill 文件夹</label>
        <div className="skill-import">
          <input
            id="skill-path"
            value={path}
            placeholder="D:\\skills\\pdf-helper（文件夹里要有 SKILL.md）"
            onChange={(event) => setPath(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                void doImport()
              }
            }}
          />
          <button
            type="button"
            className="button button-primary"
            disabled={importing}
            onClick={() => void doImport()}
          >
            {importing ? '正在导入…' : '导入'}
          </button>
        </div>
        <span className="hint">
          标准 skill 目录（SKILL.md + scripts、references），和 Claude Code、Codex 通用。导入时会把
          整个文件夹复制进数据目录，之后改原文件夹不会影响已导入的。对话里输入 /技能名
          直接触发，或由模型自行调用。
        </span>
        {error !== null && <div className="form-error">{error}</div>}
      </div>

      {loadError !== null && <div className="form-error">{loadError}</div>}
      {skills === null ? null : skills.length === 0 ? (
        <p className="setup-empty">还没有技能。把一个含 SKILL.md 的文件夹导入进来。</p>
      ) : (
        <ul className="skills-grid">
          {skills.map((skill) => (
            <SkillCard key={skill.name} skill={skill} onToggle={toggle} onDelete={remove} />
          ))}
        </ul>
      )}
    </section>
  )
}

function SkillCard({
  skill,
  onToggle,
  onDelete,
}: {
  skill: SkillInfo
  onToggle(skill: SkillInfo, enabled: boolean): Promise<void>
  onDelete(skill: SkillInfo): Promise<void>
}) {
  return (
    <li className={`skill-card${skill.broken ? ' is-broken' : ''}`}>
      <div className="skill-head">
        <span className="skill-name">/{skill.name}</span>
        <span className="spacer" />
        {skill.broken ? null : (
          <button
            type="button"
            role="switch"
            aria-checked={skill.enabled}
            aria-label={skill.enabled ? `停用 /${skill.name}` : `启用 /${skill.name}`}
            className={`skill-switch${skill.enabled ? ' is-on' : ''}`}
            title={skill.enabled ? '已启用' : '已停用'}
            onClick={() => void onToggle(skill, !skill.enabled)}
          >
            <span className="skill-knob" />
          </button>
        )}
        <button
          type="button"
          className="icon-button"
          title="删除"
          aria-label={`删除 /${skill.name}`}
          onClick={() => void onDelete(skill)}
        >
          <TrashIcon size={15} />
        </button>
      </div>
      {skill.broken ? (
        <span className="skill-error">{skill.error}</span>
      ) : (
        <span className="skill-description">{skill.description}</span>
      )}
    </li>
  )
}

function describe(cause: unknown): string {
  if (cause instanceof ApiError || cause instanceof Error) {
    return cause.message
  }
  return '出了点问题，请再试一次。'
}

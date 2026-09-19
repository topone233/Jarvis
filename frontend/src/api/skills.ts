/**
 * Skill calls: list, import from a local folder path, switch, delete.
 *
 * The import is a path, not an upload: the app and the skill folder live on
 * the same machine, and the backend validates the folder's SKILL.md and
 * copies the whole tree into the data directory, so what arrives here is the
 * installed skill - not an echo of the source folder.
 */

import { api } from './client'
import type { SkillInfo } from './types'

export function listSkills(): Promise<SkillInfo[]> {
  return api<SkillInfo[]>('/api/skills')
}

/** The import body, or the first thing wrong with the box. */
export function importPayload(path: string): { path: string } | string {
  const trimmed = path.trim()
  if (trimmed === '') {
    return '请填写 skill 文件夹的路径。'
  }
  return { path: trimmed }
}

export function importSkill(path: string): Promise<SkillInfo> {
  const payload = importPayload(path)
  if (typeof payload === 'string') {
    return Promise.reject(new Error(payload))
  }
  return api<SkillInfo>('/api/skills/import', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function setSkillEnabled(name: string, enabled: boolean): Promise<SkillInfo> {
  return api<SkillInfo>(`/api/skills/${encodeURIComponent(name)}/enabled`, {
    method: 'PUT',
    body: JSON.stringify({ enabled }),
  })
}

export function deleteSkill(name: string): Promise<void> {
  return api<void>(`/api/skills/${encodeURIComponent(name)}`, { method: 'DELETE' })
}

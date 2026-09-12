/**
 * First-run setup: where the data lives, and which model to talk to.
 *
 * Also reachable later from the sidebar, because choosing a different model
 * afterwards is the same two questions.
 *
 * The connection test is offered, never enforced: an endpoint might not expose
 * `/models` at all, and refusing to save over that would be the app deciding
 * something it cannot know.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router'

import { createModelProfile, setupDataDirectory, testModelProfile } from '../api/endpoints'
import { ApiError } from '../api/client'

export function SetupPage({ onConfigured }: { onConfigured(): void }) {
  const navigate = useNavigate()
  const [step, setStep] = useState<1 | 2>(1)

  const [dataDirectory, setDataDirectory] = useState('')
  const [name, setName] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [chatModel, setChatModel] = useState('')
  const [apiKey, setApiKey] = useState('')

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [testNote, setTestNote] = useState<string | null>(null)

  async function chooseDirectory() {
    if (dataDirectory.trim() === '') {
      setError('请填写一个数据目录。')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await setupDataDirectory(dataDirectory.trim())
      setStep(2)
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setBusy(false)
    }
  }

  async function saveProfile() {
    if (name.trim() === '' || baseUrl.trim() === '' || chatModel.trim() === '') {
      setError('名称、接口地址和模型名都要填。')
      return
    }
    setBusy(true)
    setError(null)
    setTestNote(null)
    try {
      const profile = await createModelProfile({
        name: name.trim(),
        base_url: baseUrl.trim(),
        chat_model: chatModel.trim(),
        api_key: apiKey.trim() === '' ? null : apiKey.trim(),
        is_default: true,
      })
      onConfigured()
      try {
        const result = await testModelProfile(profile.id)
        setTestNote(`连接正常，这个接口提供 ${result.models.length} 个模型。`)
      } catch (cause) {
        setTestNote(`已经保存，但连接测试没有通过：${describe(cause)}`)
      }
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="setup">
      <div className="setup-card">
        <h1 className="setup-title">配置 Jarvis</h1>
        <p className="setup-subtitle">两步就好，之后随时可以在左侧「设置」里改。</p>

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

        {step === 1 ? (
          <>
            <div className="field">
              <label htmlFor="data-directory">数据目录</label>
              <input
                id="data-directory"
                value={dataDirectory}
                placeholder="D:\\JarvisData"
                onChange={(event) => setDataDirectory(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    void chooseDirectory()
                  }
                }}
              />
              <span className="hint">
                对话、记忆、知识库都会存在这里。目录不存在会自动创建，路径不要求提前存在。
              </span>
            </div>
            <div className="setup-actions">
              <span className="spacer" />
              <button
                type="button"
                className="button button-primary"
                disabled={busy}
                onClick={() => void chooseDirectory()}
              >
                下一步
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="field">
              <label htmlFor="profile-name">配置名称</label>
              <input
                id="profile-name"
                value={name}
                placeholder="本地模型"
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="profile-base-url">接口地址</label>
              <input
                id="profile-base-url"
                value={baseUrl}
                placeholder="https://api.deepseek.com/v1"
                onChange={(event) => setBaseUrl(event.target.value)}
              />
              <span className="hint">OpenAI 兼容的接口根地址，通常以 /v1 结尾。</span>
            </div>
            <div className="field">
              <label htmlFor="profile-chat-model">模型名</label>
              <input
                id="profile-chat-model"
                value={chatModel}
                placeholder="deepseek-chat"
                onChange={(event) => setChatModel(event.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="profile-api-key">API Key</label>
              <input
                id="profile-api-key"
                type="password"
                value={apiKey}
                placeholder="本地模型可以留空"
                onChange={(event) => setApiKey(event.target.value)}
              />
              <span className="hint">保存在系统的凭据管理器里，不写进数据库。</span>
            </div>
            <div className="setup-actions">
              <button
                type="button"
                className="button button-ghost"
                disabled={busy}
                onClick={() => {
                  setError(null)
                  setStep(1)
                }}
              >
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
              {testNote !== null && (
                <button
                  type="button"
                  className="button button-primary"
                  onClick={() => navigate('/')}
                >
                  开始使用
                </button>
              )}
            </div>
          </>
        )}

        {error !== null && <div className="form-error">{error}</div>}
        {testNote !== null && <div className="form-ok">{testNote}</div>}
      </div>
    </div>
  )
}

function describe(cause: unknown): string {
  if (cause instanceof ApiError || cause instanceof Error) {
    return cause.message
  }
  return '出了点问题，请再试一次。'
}

/**
 * 快速呼出弹窗：全局快捷键唤起，纯文本即记即存。
 *
 * 这是"不打断手头的事"的入口——参考系统便签的样式：新建/打开两个页签、
 * 标题可选、正文自动伸缩、底部字数，保存即关。「打开」里点一条便签就地
 * 切到 NoteDetail 查看/编辑（NoteDetail 自己的生命周期），这里不导航——
 * 桌面壳的弹窗窗口加载的是 /popup/notepad，一旦页内跳走，整个应用会被
 * 塞进 520px 的小窗、唤出从此全废（2026-10-08 事故）。
 *
 * 快捷键从插件配置里读（capture_hotkey），插件设置变化时重读；解析不出
 * 有效的组合键就不挂监听，而不是猜一个。
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type MouseEvent,
} from "react";

import { useToast } from "../../../frontend/src/hooks/useToast";
import { CloseIcon, PinIcon } from "../../../frontend/src/components/icons";
import {
  createNote,
  listNotes,
  readPluginConfig,
  type NoteSummary,
} from "./api";
import { NoteDetail } from "./NoteDetail";
import {
  formatHotkey,
  hotkeyMatches,
  parseHotkey,
  type HotkeyCombo,
} from "../../../frontend/src/plugins/hotkey";
import { PLUGINS_CHANGED_EVENT } from "../../../frontend/src/plugins/registry";
import {
  popupPluginId,
  usePopupPin,
} from "../../../frontend/src/plugins/shell";
import { relativeTime } from "./time";

const RECENTS_LIMIT = 8;

export function QuickCapture() {
  const toast = useToast();

  const [combo, setCombo] = useState<HotkeyCombo | null>(null);
  const [comboLabel, setComboLabel] = useState("");
  const [open, setOpen] = useState(false);
  const [viewingId, setViewingId] = useState<string | null>(null);
  const [tab, setTab] = useState<"new" | "open">("new");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [recents, setRecents] = useState<NoteSummary[] | null>(null);
  const [saving, setSaving] = useState(false);

  const titleRef = useRef<HTMLInputElement>(null);
  const areaRef = useRef<HTMLTextAreaElement>(null);

  // The configured summon key, re-read whenever plugin settings change.
  useEffect(() => {
    let alive = true;
    async function read() {
      let label = "Alt+N";
      let parsed: HotkeyCombo | null = parseHotkey("Alt+N");
      try {
        const config = await readPluginConfig();
        const text =
          typeof config.capture_hotkey === "string"
            ? config.capture_hotkey
            : "Alt+N";
        parsed = parseHotkey(text);
        label = parsed === null ? text : formatHotkey(parsed);
      } catch {
        // The app is not set up (or the plugin is off) - the shipped default
        // still stands; nothing here is worth an error toast.
      }
      if (alive) {
        setCombo(parsed);
        setComboLabel(label);
      }
    }
    void read();
    window.addEventListener(PLUGINS_CHANGED_EVENT, read);
    return () => {
      alive = false;
      window.removeEventListener(PLUGINS_CHANGED_EVENT, read);
    };
  }, []);

  // The summon key toggles the popup from anywhere. `active` holds the
  // narrowed combo: TS will not carry a narrowing into a hoisted function
  // declaration, and onKeyDown below is one.
  useEffect(() => {
    if (combo === null) {
      return;
    }
    const active = combo;
    function onKeyDown(event: KeyboardEvent) {
      if (hotkeyMatches(active, event)) {
        event.preventDefault();
        setOpen((current) => !current);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [combo]);

  // While open: Esc closes (an IME mid-composition Esc belongs to the IME),
  // focus lands in the title, and the 打开 tab's list gets one fresh read.
  const loadRecents = useCallback(() => {
    listNotes({ limit: RECENTS_LIMIT })
      .then((page) => setRecents(page.items))
      .catch(() => setRecents([]));
  }, []);

  useEffect(() => {
    if (!open) {
      return;
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !event.isComposing) {
        setOpen(false);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    titleRef.current?.focus();
    loadRecents();
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, loadRecents]);

  // 卡片关闭时详情一并归位：Esc、✕、保存后自动收起、壳的合成热键收窗，
  // 走到哪条路，下次唤出（或主窗口里再开）都是干净的卡片。
  useEffect(() => {
    if (!open) {
      setViewingId(null);
    }
  }, [open]);

  // Grow with the content, capped so a pasted essay cannot cover the screen.
  useEffect(() => {
    const area = areaRef.current;
    if (area === null) {
      return;
    }
    area.style.height = "auto";
    area.style.height = `${Math.min(area.scrollHeight, 320)}px`;
  }, [content, open]);

  const canSave = title.trim() !== "" || content.trim() !== "";
  const firstLine = content.trim().split("\n")[0]?.slice(0, 16) ?? "";
  // 桌面壳的弹窗窗口里，卡片页签行就是标题条：拖它挪窗口，图钉管置顶。
  // 拖拽区「只认直接目标」（桌面壳的全局设置），所以行内空白 spacer 也要
  // 挂类；按钮、输入框是子元素，天然不被拖拽吞掉。主窗口的页内卡片不变。
  const inPopup = popupPluginId(window.location.pathname) !== null;
  const popupPin = usePopupPin();
  const dragRegion = inPopup ? " pywebview-drag-region" : "";

  async function save() {
    if (saving) {
      return;
    }
    setSaving(true);
    try {
      const note = await createNote({
        title: title.trim(),
        content,
        source: "popup",
      });
      toast.show(
        note.tag_status === "pending" ? "已保存，AI 打标签中…" : "已保存",
      );
      setTitle("");
      setContent("");
      setTab("new");
      setRecents(null);
      setOpen(false);
    } catch (cause) {
      toast.show(
        cause instanceof Error ? cause.message : "保存失败，请再试一次。",
        "bad",
      );
    } finally {
      setSaving(false);
    }
  }

  function openNote(noteId: string) {
    // 就地切到详情，绝不在弹窗窗口里导航——见文件头的事故注。
    setViewingId(noteId);
  }

  function backFromNote() {
    setViewingId(null);
    loadRecents(); // 编辑过的标题和时间要在列表里反映出来
  }

  // 点在遮罩上（卡片外）收卡片；主窗口内的弹出和弹窗窗口里都一样。
  function closeOnMask(event: MouseEvent<HTMLDivElement>) {
    if (event.target === event.currentTarget) {
      setOpen(false);
    }
  }

  if (!open) {
    return null;
  }

  if (viewingId !== null) {
    // 详情态：同一张卡片，内容交给 NoteDetail（头部、冲突横幅、编辑器）。
    return (
      <div className="quick-note" onClick={closeOnMask}>
        <div className="quick-note-box" role="dialog" aria-label="快速记一条">
          <NoteDetail
            noteId={viewingId}
            onBack={backFromNote}
            onClose={() => setOpen(false)}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="quick-note" onClick={closeOnMask}>
      <div className="quick-note-box" role="dialog" aria-label="快速记一条">
        <div className={`quick-note-tabs${dragRegion}`}>
          <button
            type="button"
            className={`quick-note-tab${tab === "new" ? " is-on" : ""}`}
            onClick={() => setTab("new")}
          >
            新建
          </button>
          <button
            type="button"
            className={`quick-note-tab${tab === "open" ? " is-on" : ""}`}
            onClick={() => setTab("open")}
          >
            打开
          </button>
          <span className={`spacer${dragRegion}`} />
          <span className="quick-note-hotkey">{comboLabel}</span>
          {popupPin !== null && (
            <button
              type="button"
              className={`icon-button${popupPin.pinned ? " is-pinned" : ""}`}
              aria-label={
                popupPin.pinned ? "取消固定" : "固定（切到其他应用时保持显示）"
              }
              aria-pressed={popupPin.pinned}
              title={
                popupPin.pinned ? "取消固定" : "固定：切到其他应用时保持显示"
              }
              onClick={popupPin.toggle}
            >
              <PinIcon size={15} />
            </button>
          )}
          <button
            type="button"
            className="icon-button"
            aria-label="关闭"
            onClick={() => setOpen(false)}
          >
            <CloseIcon size={15} />
          </button>
        </div>

        {tab === "new" ? (
          <>
            <input
              ref={titleRef}
              className="quick-note-title"
              value={title}
              placeholder="标题（可选）"
              onChange={(event) => setTitle(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  areaRef.current?.focus();
                }
              }}
            />
            <textarea
              ref={areaRef}
              className="quick-note-content"
              value={content}
              placeholder="写点什么……"
              onChange={(event) => setContent(event.target.value)}
            />
            <div className="quick-note-foot">
              <span className="quick-note-count">
                {content.length} 字 · {firstLine === "" ? "空" : firstLine}
              </span>
              <span className="spacer" />
              <button
                type="button"
                className="button button-ghost button-small"
                disabled={!canSave || saving}
                onClick={() => {
                  setTitle("");
                  setContent("");
                  titleRef.current?.focus();
                }}
              >
                清空
              </button>
              <button
                type="button"
                className="button button-primary button-small"
                disabled={!canSave || saving}
                onClick={() => void save()}
              >
                {saving ? "保存中…" : "保存"}
              </button>
            </div>
          </>
        ) : (
          <div className="quick-note-recents">
            {recents === null && <p className="quick-note-empty">加载中…</p>}
            {recents !== null && recents.length === 0 && (
              <p className="quick-note-empty">还没有便签。</p>
            )}
            {recents?.map((note) => (
              <button
                key={note.id}
                type="button"
                className="quick-note-row"
                onClick={() => openNote(note.id)}
              >
                <span className="quick-note-row-title">
                  {note.title || note.excerpt || "（无标题）"}
                </span>
                <span className="quick-note-row-time">
                  {relativeTime(note.updated_at)}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

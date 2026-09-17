# Jarvis

（**J**ust **A** **R**ather **V**ery **I**ntelligent **S**ystem）

个人用的本地 AI 助手，B/S 架构，只考虑 Windows。前后端都跑在自己机器上，数据放在你自己指定的目录里，模型走任何 OpenAI Chat Completions 兼容的接口。

## 现状

| 部分 | 状态 |
| --- | --- |
| 后端 `backend/` | API 完整，四段门禁全绿 |
| 前端 `frontend/` | 第一刀：对话区（含首次配置）能跑通真实后端 |
| 其余界面 | 未开始，见下方"还没有的" |

已经能用的：选数据目录 → 配模型 → 建会话 → 发消息 → 流式回复 → 刷新续传 → 终止 / 重新生成 / 点赞点踩 → 会话增删与切换 → 模型配置与会话期增删改 → 在输入框里换模型、拨思考强度 → 记忆页（查看 / 编辑 / 删除 / 恢复 / 永久删除）。

记忆是搭主回复的车：主模型在回答的同时通过原生 tool call（`save_memory` / `forget_memory`）表达要记或要忘什么，不回传结果、不加额外调用，后端在回答完成时执行并落库；屏幕上「写入记忆」这一步只在真有动作时出现，展开能看到每一条。

**还没有的**（后端接口在，界面没做）：右侧对话目录、项目页、知识库页、输入框的 `+` 附件菜单、Slash 命令、Mermaid / SVG / HTML 预览卡片。

## 技术栈

版本全部锁死（无 `^` `~`），`package-lock.json` 与 `uv.lock` 一并提交。

| | 版本 | 说明 |
| --- | --- | --- |
| Python | `>=3.12,<3.13` | 环境由 uv 管理 |
| Node | `^22.12.0 \|\| ^24.0.0 \|\| >=26.0.0` | vite 与 vitest 的 engines 取更严者 |
| FastAPI / uvicorn | `0.115.12` / `0.34.0` | |
| React / Vite / TypeScript | `19.3.0` / `8.3.0` / `6.0.3` | Vite 8 走 rolldown |
| 路由 / Markdown | `react-router@8.3.1` / `react-markdown@10.1.0` | v8 起 `react-router` 就是主包 |

后端唯一的运行时依赖里有一个 `keyring`：API Key 写进 Windows 凭据管理器，**不进数据库、不进任何配置文件**。接口只回一个 `has_api_key`，读不回明文。

## 跑起来

首次需要 `uv`、Node、npm。后端在 `backend/` 下首次 `uv sync --group dev` 会自动建好虚拟环境，前端 `npm ci`。

### 开发：两个进程

```powershell
# 终端 1 —— 后端 8787，带热重载
.\scripts\start-core.ps1

# 终端 2 —— 前端 5173，/api 反代到 8787
cd frontend
npm run dev
```

打开 http://127.0.0.1:5173 。Vite 把 `/api` 代理到 `127.0.0.1:8787`，所以开发和生产是同一个源，客户端代码里没有环境分支，也没有 CORS。要指向别的后端就设 `JARVIS_CORE_URL`。

### 生产：一个进程

```powershell
cd frontend
npm run build

cd ..\backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 8787
```

`frontend/dist` 存在时后端会把它挂到 `/`，SPA 路由回落到 `index.html`；`dist` 不存在就跳过（所以开发态和测试都不受影响）。打错的 `/api/...` 一律回 JSON 404，不会返回一坨 HTML 让客户端在很远的地方报解析错。

### 第一次打开

界面会引导你做两件事：

1. **选数据目录** —— 必须是一个**已经存在**的目录。程序不会替你创建：目录名打错时自动建一个空目录，和"目录没了"在屏幕上长得一模一样，这个歧义由你来消掉，不由程序猜。
2. **配模型** —— base_url、模型名、API Key，有"测试连接"按钮。

以后在设置页可以把数据目录换掉。**换目录等于换一整套数据**：会话、记忆、知识库、回收站全在所选目录里，`GET /api/health` 会把它当前正在看的目录原样报出来，设置页也把它显示在输入框上方——这一条是刻意明说的，不是暗示。

启动时如果目录已经不在（盘没挂、被删、被改名），程序**不会重建它**，而是把缺的是哪个目录直接报出来，前端照原样显示。

## 门禁

改完必须全绿，命令与 CI 一致，不要在命令行上加参数。

```powershell
# 后端
cd backend
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest

# 前端
cd frontend
npm run typecheck
npm run lint
npm run format:check
npm test
npm run build
```

`[tool.ruff]` / `[tool.mypy]` 与 vitest 的配置是规则的唯一出处。前端单测是 node 环境下的纯逻辑测试（`src/sse/`、`src/runs/`、`src/api/`），没有 jsdom，也不跑浏览器——跨进程的场景（杀后端、刷新续传）靠手工验收。

## 文档

| 文件 | 内容 |
| --- | --- |
| [docs/Spec.md](docs/Spec.md) | 产品定义：四层上下文模型、记忆读写分离、UI 规格、一次回复的生命周期 |
| [docs/Core-API.md](docs/Core-API.md) | 后端 API 契约：SSE 事件词汇、run 的续传与中断语义、回收站 |
| [backend/README.md](backend/README.md) | 后端的开发与门禁说明 |
| [docs/Lessons-Learned.md](docs/Lessons-Learned.md) | 踩过的坑与由此定下的规矩，写代码前值得先扫一眼 |

## 几条设计约定

不是偏好，是踩过之后定下来的，改代码前先看一眼有没有踩到：

- **一次回复不属于发起它的那个 HTTP 连接。** 关掉页面、切走、刷新，回答都继续生成并落库；客户端回来先问 `GET /api/runs/{run_id}` 还在不在跑，再接 `/stream`。文本是**累积快照**，所以接上既不会重字也不会漏字，刷新和没刷新走的是同一条渲染路径。
- **前端只显示后端真正发生的事。** 进度条由收到的 `audit` / `reasoning.delta` 事件驱动，没发生的阶段不占位；刷新后用 `GET /api/runs/{run_id}/events` 补齐错过的轨迹。不写死阶段清单。
- **消息不单独删除。** 删除的单位是会话，所以一轮问答不会只剩一半，重新生成也就可以假定最新一条回复一定在。
- **删除的单位与会话有关：会话是永久删除，记忆是先进已删除区。** 从侧边栏删掉的对话连同消息、run 日志、审计事件、压缩记录、点赞点踩一起物理删除，删错无法恢复；记忆的删除是软删除，进记忆页的「已删除」区，可恢复，也可在那里永久删除。回收站没有页面——这是 2026-09-14 的决定：对话删除不留垃圾，记忆的找回走记忆页。
- **数据目录不自动创建**，见上文"第一次打开"。

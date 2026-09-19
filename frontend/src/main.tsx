import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'

import { App } from './App'
import { ToastProvider } from './components/Toast'
// The code font, bundled so every machine reads the same code. The css files
// carry their own unicode-range subsets, so CJK text never downloads them.
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/700.css'
import './styles/tokens.css'
import './styles/app.css'
import './styles/chat.css'

const container = document.getElementById('root')
if (container === null) {
  throw new Error('index.html is missing the #root element.')
}

createRoot(container).render(
  <StrictMode>
    <BrowserRouter>
      {/* Above `App` rather than inside it, so that every screen `App` can
          return has toasts - including the first-run wizard, which is not
          inside the shell. */}
      <ToastProvider>
        <App />
      </ToastProvider>
    </BrowserRouter>
  </StrictMode>,
)

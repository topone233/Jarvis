import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'

import { App } from './App'
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
      <App />
    </BrowserRouter>
  </StrictMode>,
)

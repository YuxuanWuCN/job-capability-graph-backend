import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

export type ViewPreference = 'auto' | 'mobile' | 'desktop'
export type EffectiveViewMode = 'mobile' | 'desktop'

const VIEW_MODE_KEY = 'job_graph_view_mode'
const PROMPT_DISMISSED_KEY = 'job_graph_view_prompt_dismissed'
const MOBILE_MEDIA_QUERY = '(max-width: 768px)'

interface ViewModeContextValue {
  preference: ViewPreference
  effectiveMode: EffectiveViewMode
  isMobile: boolean
  showDevicePrompt: boolean
  selectPreference: (next: ViewPreference) => void
  dismissPrompt: () => void
}

const ViewModeContext = createContext<ViewModeContextValue | null>(null)

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStorage(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // Ignore storage failures in restricted browsing contexts.
  }
}

function parseQueryPreference(): ViewPreference | null {
  if (typeof window === 'undefined') return null
  const raw = new URLSearchParams(window.location.search).get('view')?.trim().toLowerCase()
  if (raw === 'mobile') return 'mobile'
  if (raw === 'pc' || raw === 'desktop') return 'desktop'
  if (raw === 'auto') return 'auto'
  return null
}

function readInitialState(): { preference: ViewPreference; promptDismissed: boolean } {
  if (typeof window === 'undefined') {
    return { preference: 'auto', promptDismissed: false }
  }
  const fromQuery = parseQueryPreference()
  if (fromQuery) {
    writeStorage(VIEW_MODE_KEY, fromQuery)
    writeStorage(PROMPT_DISMISSED_KEY, '1')
    return { preference: fromQuery, promptDismissed: true }
  }
  const stored = readStorage(VIEW_MODE_KEY)
  const preference: ViewPreference =
    stored === 'mobile' || stored === 'desktop' || stored === 'auto' ? stored : 'auto'
  const promptDismissed = readStorage(PROMPT_DISMISSED_KEY) === '1' || preference !== 'auto'
  return { preference, promptDismissed }
}

function isPhysicalMobile(): boolean {
  if (typeof window === 'undefined') return false
  const screenWidth = window.screen?.width || window.innerWidth
  return Math.min(screenWidth, window.innerWidth) <= 768
}

export function ViewModeProvider({ children }: { children: ReactNode }) {
  const initial = useMemo(() => readInitialState(), [])
  const [preference, setPreference] = useState<ViewPreference>(initial.preference)
  const [promptDismissed, setPromptDismissed] = useState<boolean>(initial.promptDismissed)
  const [isNarrowScreen, setIsNarrowScreen] = useState<boolean>(() =>
    typeof window !== 'undefined' ? window.matchMedia(MOBILE_MEDIA_QUERY).matches : false,
  )

  useEffect(() => {
    const query = window.matchMedia(MOBILE_MEDIA_QUERY)
    setIsNarrowScreen(query.matches)
    const handleChange = (event: MediaQueryListEvent) => {
      setIsNarrowScreen(event.matches)
    }
    query.addEventListener('change', handleChange)
    return () => query.removeEventListener('change', handleChange)
  }, [])

  const effectiveMode: EffectiveViewMode =
    preference === 'auto' ? (isNarrowScreen ? 'mobile' : 'desktop') : preference
  const isMobile = effectiveMode === 'mobile'

  useEffect(() => {
    document.documentElement.dataset.viewMode = effectiveMode
    document.documentElement.dataset.viewPreference = preference

    const viewportMeta = document.querySelector<HTMLMetaElement>('meta[name="viewport"]')
    if (!viewportMeta) return

    if (preference === 'desktop' && isPhysicalMobile()) {
      viewportMeta.content =
        'width=1280, initial-scale=0.32, minimum-scale=0.25, maximum-scale=3.0, user-scalable=yes'
    } else {
      viewportMeta.content = 'width=device-width, initial-scale=1.0, viewport-fit=cover'
    }
  }, [effectiveMode, preference])

  const selectPreference = (next: ViewPreference) => {
    setPreference(next)
    writeStorage(VIEW_MODE_KEY, next)
    setPromptDismissed(true)
    writeStorage(PROMPT_DISMISSED_KEY, '1')
  }

  const dismissPrompt = () => {
    setPromptDismissed(true)
    writeStorage(PROMPT_DISMISSED_KEY, '1')
  }

  const showDevicePrompt = !promptDismissed && preference === 'auto'

  return (
    <ViewModeContext.Provider
      value={{
        preference,
        effectiveMode,
        isMobile,
        showDevicePrompt,
        selectPreference,
        dismissPrompt,
      }}
    >
      {children}
    </ViewModeContext.Provider>
  )
}

export function useViewMode() {
  const ctx = useContext(ViewModeContext)
  if (!ctx) throw new Error('useViewMode must be used inside ViewModeProvider')
  return ctx
}

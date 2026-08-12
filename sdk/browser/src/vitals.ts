export type Measurements = Record<string, { value: number; unit: string }>

interface LayoutShift extends PerformanceEntry {
  value: number
  hadRecentInput: boolean
}

/**
 * Core Web Vitals, from the browser's own timeline.
 *
 * Measured rather than modelled: every number here comes from a PerformanceObserver entry the
 * browser produced, so it is what the reader actually experienced rather than what a synthetic
 * run would have.
 *
 * Everything is wrapped, because entry types vary by browser and an SDK that throws on an
 * unsupported one has broken the page it was meant to observe.
 */
export function collectVitals(onChange: (measurements: Measurements) => void): () => void {
  const measurements: Measurements = {}
  const observers: PerformanceObserver[] = []

  const record = (name: string, value: number, unit = 'millisecond') => {
    measurements[name] = { value, unit }
    onChange(measurements)
  }

  const observe = (type: string, handler: (list: PerformanceObserverEntryList) => void) => {
    try {
      const observer = new PerformanceObserver(handler)
      // buffered: the observer is registered after the browser has already recorded the
      // entries we care most about. Without it, LCP and FCP are simply missing.
      observer.observe({ type, buffered: true } as PerformanceObserverInit)
      observers.push(observer)
    } catch {
      // This browser does not report this entry type. One missing vital, not a broken SDK.
    }
  }

  // LCP keeps being revised upward until the reader interacts, so the last entry wins.
  observe('largest-contentful-paint', (list) => {
    const entries = list.getEntries()
    const last = entries[entries.length - 1]
    if (last) record('lcp', last.startTime)
  })

  observe('paint', (list) => {
    for (const entry of list.getEntries()) {
      if (entry.name === 'first-contentful-paint') record('fcp', entry.startTime)
    }
  })

  // CLS accumulates: it is the sum of every unexpected shift, not the worst one.
  let cls = 0
  observe('layout-shift', (list) => {
    for (const entry of list.getEntries() as LayoutShift[]) {
      // A shift within 500ms of an interaction is one the reader caused — expanding an
      // accordion is not the page moving under them.
      if (!entry.hadRecentInput) cls += entry.value
    }
    record('cls', Number(cls.toFixed(4)), '')
  })

  // INP is the worst interaction, because one unbearable click is what people remember.
  let inp = 0
  observe('event', (list) => {
    for (const entry of list.getEntries()) {
      const duration = entry.duration
      if (duration > inp) {
        inp = duration
        record('inp', duration)
      }
    }
  })

  const navigation = performance.getEntriesByType('navigation')[0] as
    PerformanceNavigationTiming | undefined
  if (navigation) {
    record('ttfb', navigation.responseStart)
  }

  return () => {
    for (const observer of observers) observer.disconnect()
  }
}

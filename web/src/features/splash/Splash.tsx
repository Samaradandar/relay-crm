import { useCallback, useEffect, useRef, useState } from 'react';
import { BRAND } from '@/config/brand';
import './splash.css';

/**
 * Opening sequence, played once per browser session:
 *   intro  — glow pulses, the R mark opens from its centre with a spring, the wordmark wipes in,
 *            the tagline rises letter by letter, a loading line fills
 *   seam   — a light seam opens down the centre of the screen
 *   split  — the screen (logo included) parts down the middle like two doors and the page behind rises in
 * Click, Enter or Esc skips straight to the split. Reduced-motion users get a short fade.
 */
type Phase = 'intro' | 'seam' | 'split' | 'done';
const T = { seam: 2300, split: 2750, done: 3700 };

export function shouldPlaySplash(): boolean {
  try { return sessionStorage.getItem('relay.splashSeen') !== '1'; } catch { return true; }
}

export function Splash({ onDone }: { onDone: () => void }) {
  const reduce = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const [phase, setPhase] = useState<Phase>('intro');
  const timers = useRef<number[]>([]);

  const go = useCallback((p: Phase) => {
    setPhase(p);
    document.documentElement.dataset.splash = p === 'done' ? 'off' : p === 'split' ? 'split' : 'on';
    if (p === 'done') { try { sessionStorage.setItem('relay.splashSeen', '1'); } catch { /* ignore */ } onDone(); }
  }, [onDone]);

  const skip = useCallback(() => {
    if (phase === 'split' || phase === 'done') return;
    timers.current.forEach(clearTimeout);
    go('split');
    timers.current = [window.setTimeout(() => go('done'), reduce ? 300 : T.done - T.split)];
  }, [phase, go, reduce]);

  useEffect(() => {
    document.documentElement.dataset.splash = 'on';
    const at = (ms: number, p: Phase) => window.setTimeout(() => go(p), ms);
    timers.current = reduce ? [at(1200, 'split'), at(1500, 'done')] : [at(T.seam, 'seam'), at(T.split, 'split'), at(T.done, 'done')];
    const onKey = (e: KeyboardEvent) => { if (['Escape', 'Enter', ' '].includes(e.key)) skip(); };
    window.addEventListener('keydown', onKey);
    return () => { timers.current.forEach(clearTimeout); window.removeEventListener('keydown', onKey); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (phase === 'done') return null;
  return (
    <div className={`splash splash--${phase}${reduce ? ' splash--reduced' : ''}`} onClick={skip} role="presentation">
      <div className="splash__half splash__half--left" aria-hidden="true"><Stage /></div>
      <div className="splash__half splash__half--right" aria-hidden="true"><Stage /></div>
      <div className="splash__seam" aria-hidden="true" />
      <div className="splash__flash" aria-hidden="true" />
      <p className="sr-only" role="status">{BRAND.name} is opening. {BRAND.tagline}.</p>
      <button type="button" className="splash__skip" onClick={(e) => { e.stopPropagation(); skip(); }}>Skip</button>
    </div>
  );
}

/** The full-screen picture. Rendered twice (one per half) so the logo splits with the screen. */
function Stage() {
  return (
    <div className="splash__stage">
      <div className="splash__glow" />
      <div className="splash__grid" />
      <div className="splash__orbit splash__orbit--a" />
      <div className="splash__orbit splash__orbit--b" />
      <div className="splash__center">
        <img className="splash__mark" src={BRAND.logo.mark} alt="" draggable={false} />
        <img className="splash__wordmark" src={BRAND.logo.wordmark} alt="" draggable={false} />
        <p className="splash__tagline">
          {Array.from(BRAND.tagline).map((ch, i) => (
            <span key={i} style={{ animationDelay: `${1350 + i * 22}ms` }}>{ch === ' ' ? ' ' : ch}</span>
          ))}
        </p>
        <div className="splash__progress"><span /></div>
      </div>
    </div>
  );
}

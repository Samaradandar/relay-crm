import type { Config } from 'tailwindcss';

// Every colour, radius and font maps to a token from the Relay CRM design system (src/styles/tokens.css).
const v = (n: string) => `var(--${n})`;
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: ['selector', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        surface: { 0: v('surface-0'), 1: v('surface-1'), 2: v('surface-2'), 3: v('surface-3') },
        line: { DEFAULT: v('border'), control: v('border-control') },
        ink: { DEFAULT: v('ink'), 2: v('ink-2'), 3: v('ink-3') },
        brand: { DEFAULT: v('brand'), hover: v('brand-hover'), soft: v('brand-soft'), ink: v('brand-ink'), on: v('on-brand') },
        navy: { DEFAULT: v('navy'), on: v('on-navy') },
        accent: { DEFAULT: v('accent'), soft: v('accent-soft'), ink: v('accent-ink'), on: v('on-accent') },
        gold: { DEFAULT: v('gold'), soft: v('gold-soft'), ink: v('gold-ink'), on: v('on-gold') },
        amber: v('amber'),
        success: { DEFAULT: v('success'), soft: v('success-soft') },
        warning: { DEFAULT: v('warning'), soft: v('warning-soft') },
        danger: { DEFAULT: v('danger'), soft: v('danger-soft') },
        focus: v('focus')
      },
      backgroundImage: { 'gradient-deep': v('gradient-deep'), 'gradient-cinematic': v('gradient-cinematic'), 'gradient-metallic': v('gradient-metallic'), 'gradient-white': v('gradient-white') },
      borderRadius: { sm: v('radius-sm'), md: v('radius-md'), lg: v('radius-lg'), pill: v('radius-pill') },
      fontFamily: { sans: v('font-sans').split(','), mono: v('font-mono').split(',') },
      boxShadow: { card: v('shadow-card'), drag: v('shadow-drag'), pop: v('shadow-pop') },
      spacing: { sidebar: v('sidebar-width'), topbar: v('topbar-height') }
    }
  },
  plugins: []
} satisfies Config;

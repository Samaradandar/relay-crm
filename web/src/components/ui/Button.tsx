import clsx from 'clsx';
import type { ButtonHTMLAttributes } from 'react';
import { Icon, type IconName } from './Icon';

type Props = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'quiet' | 'danger'; size?: 'sm' | 'md'; icon?: IconName; loading?: boolean };

const V = {
  primary: 'bg-brand text-brand-on hover:bg-brand-hover',
  secondary: 'bg-surface-1 text-ink border-line-control hover:bg-surface-3',
  quiet: 'bg-transparent text-ink-2 hover:bg-surface-3 hover:text-ink',
  danger: 'bg-surface-1 text-danger border-danger hover:bg-danger-soft'
};

/** One primary per view. If a role may only request an action, label it "Request approval" — never disable for role reasons. */
export function Button({ variant = 'secondary', size = 'md', icon, loading, className, children, disabled, type = 'button', ...rest }: Props) {
  return (
    <button type={type} disabled={disabled || loading} aria-busy={loading || undefined}
      className={clsx('inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md border border-transparent font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50',
        size === 'sm' ? 'h-7 px-2.5 text-[13px]' : 'h-9 px-3.5 text-sm', V[variant], className)} {...rest}>
      {loading ? <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" /> : icon && <Icon name={icon} />}
      {children}
    </button>
  );
}

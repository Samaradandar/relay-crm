import clsx from 'clsx';
import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react';
import { Icon } from './Icon';

interface Common { label: string; hint?: ReactNode; error?: string; required?: boolean; optionalNote?: string; className?: string }
const control = 'w-full rounded-md border border-line-control bg-surface-1 px-2.5 text-sm text-ink placeholder:text-ink-3 focus:border-focus focus:outline focus:outline-2 focus:outline-focus focus:outline-offset-0 disabled:bg-surface-2';

function Wrap({ id, label, hint, error, required, optionalNote, className, children }: Common & { id: string; children: ReactNode }) {
  return (
    <div className={clsx('flex min-w-0 flex-col gap-1.5', className)}>
      <label htmlFor={id} className="text-[13px] font-semibold leading-[18px] text-ink">
        {label}{required ? <span aria-hidden="true" className="ml-0.5 text-danger">*</span> : optionalNote && <span className="ml-1 font-normal text-ink-3">{optionalNote}</span>}
      </label>
      {children}
      {error ? <p id={`${id}-d`} className="flex items-center gap-1 text-caption text-danger"><Icon name="alert" size={14} />{error}</p>
        : hint ? <p id={`${id}-d`} className="text-xs leading-4 text-ink-3">{hint}</p> : null}
    </div>
  );
}

export function TextField({ label, hint, error, required, optionalNote, className, id, ...rest }: Common & InputHTMLAttributes<HTMLInputElement>) {
  const auto = useId(); const fid = id ?? auto;
  return <Wrap {...{ id: fid, label, hint, error, required, optionalNote, className }}>
    <input id={fid} required={required} aria-invalid={!!error || undefined} aria-describedby={hint || error ? `${fid}-d` : undefined} className={clsx(control, 'h-9', error && 'border-danger')} {...rest} />
  </Wrap>;
}

export function SelectField({ label, hint, error, required, optionalNote, className, id, options, ...rest }: Common & SelectHTMLAttributes<HTMLSelectElement> & { options: { value: string; label: string }[] }) {
  const auto = useId(); const fid = id ?? auto;
  return <Wrap {...{ id: fid, label, hint, error, required, optionalNote, className }}>
    <select id={fid} required={required} aria-describedby={hint || error ? `${fid}-d` : undefined} className={clsx(control, 'h-9')} {...rest}>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  </Wrap>;
}

export function TextAreaField({ label, hint, error, required, optionalNote, className, id, ...rest }: Common & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const auto = useId(); const fid = id ?? auto;
  return <Wrap {...{ id: fid, label, hint, error, required, optionalNote, className }}>
    <textarea id={fid} required={required} aria-invalid={!!error || undefined} aria-describedby={hint || error ? `${fid}-d` : undefined} className={clsx(control, 'min-h-[72px] resize-y py-2', error && 'border-danger')} {...rest} />
  </Wrap>;
}

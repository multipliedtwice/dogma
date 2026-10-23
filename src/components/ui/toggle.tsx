import {cn} from '../../lib/utils';

export function Toggle({label, checked, onChange, disabled = false}: {label: string; checked: boolean; onChange: (value: boolean) => void; disabled?: boolean}) {
  return <label className="setting-row"><span>{label}</span>
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled}
      className={cn('switch', checked && 'on')} onClick={() => onChange(!checked)}><span/></button>
  </label>;
}

import clsx from 'clsx';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { LEAD_SOURCES, type AssignmentConfig, type RuleInput } from '@/api/types';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge, TeamChip } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Banner } from '@/components/ui/Feedback';
import { SelectField, TextField } from '@/components/ui/Field';
import { Icon } from '@/components/ui/Icon';
import { CapMeter, Dialog } from '@/components/ui/Overlay';
import { useReasonPrompt } from '@/components/ui/ReasonPrompt';
import { WAITING_LABEL } from '@/lib/leads';

const INDUSTRIES = ['Food & beverage', 'Healthcare', 'Hospitality', 'Retail', 'Automotive', 'Logistics', 'Real estate', 'Education', 'Finance', 'Fitness', 'Events', 'Construction', 'Other'];

/** P2-08 Settings > Assignment: rules in priority order (first match wins), rep groups, default team, caps, test a rule. */
export function AssignmentSettingsPage() {
  const qc = useQueryClient();
  const cfg = useQuery({ queryKey: ['assignmentConfig'], queryFn: api.assignmentConfig });
  const teams = useQuery({ queryKey: ['teams'], queryFn: api.teams });
  const users = useQuery({ queryKey: ['users'], queryFn: api.users });
  const refresh = () => ['assignmentConfig', 'pool', 'poolSummary', 'teamLoad', 'deals'].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const { ask, element } = useReasonPrompt(refresh);
  const [editing, setEditing] = useState<RuleInput | null>(null);
  const [caps, setCaps] = useState<Record<string, string>>({});
  if (!cfg.data) return <div className="panel h-64 animate-pulse" />;
  const c = cfg.data; const team = (id?: string | null) => teams.data?.find((t) => t.id === id);
  const target = (r: RuleInput) => r.target.groupId ? c.groups.find((g) => g.id === r.target.groupId)?.name : r.target.userId ? users.data?.find((u) => u.id === r.target.userId)?.name : team(r.target.teamId)?.name;

  return <div className="flex flex-col gap-6">
    <PageHeader title="Assignment settings" subtitle="How new leads leave the Lead Pool. Every change re-runs the waiting leads, oldest first, and is saved in the audit log." actions={<Button variant="primary" icon="plus" onClick={() => setEditing({ name: '', active: true, match: {}, target: {} })}>New rule</Button>} />

    <section className="flex flex-col gap-3" aria-labelledby="rules-h">
      <div><h2 id="rules-h" className="m-0 text-heading">Rules</h2><p className="m-0 text-small text-ink-2">Checked top to bottom. The first rule that matches decides where the lead goes; round-robin then runs inside that team, group or rep.</p></div>
      <ol className="m-0 flex list-none flex-col overflow-hidden rounded-lg border border-line bg-surface-1 p-0">
        {c.rules.map((r, i) => <li key={r.id} className={clsx('grid grid-cols-[32px_minmax(0,1fr)_auto] items-center gap-3 border-t border-line px-3 py-3 first:border-t-0', !r.active && 'opacity-60')}>
          <span className="grid h-7 w-7 place-items-center rounded-full bg-surface-2 text-[13px] font-semibold">{i + 1}</span>
          <span className="flex min-w-0 flex-col"><span className="flex flex-wrap items-center gap-2 text-body-strong">{r.name}{!r.active && <Badge>Off</Badge>}</span>
            <span className="text-small text-ink-2">If {Object.entries(r.match).map(([k, v]) => <b key={k} className="text-ink">{k === 'source' ? 'source' : 'industry'} is {v} </b>)}→ <b className="text-ink">{target(r)}</b></span></span>
          <span className="flex items-center gap-1">
            <Button size="sm" variant="quiet" aria-label="Move up" disabled={i === 0} onClick={() => ask({ title: `Move “${r.name}” up`, done: 'Priority changed', run: (x) => api.moveRule(r.id, -1, x) })}>↑</Button>
            <Button size="sm" variant="quiet" aria-label="Move down" disabled={i === c.rules.length - 1} onClick={() => ask({ title: `Move “${r.name}” down`, done: 'Priority changed', run: (x) => api.moveRule(r.id, 1, x) })}>↓</Button>
            <Button size="sm" variant="quiet" onClick={() => ask({ title: `Turn ${r.active ? 'off' : 'on'} “${r.name}”?`, done: `Rule turned ${r.active ? 'off' : 'on'}`, run: (x) => api.toggleRule(r.id, x) })}>{r.active ? 'Turn off' : 'Turn on'}</Button>
            <Button size="sm" onClick={() => setEditing({ id: r.id, name: r.name, active: r.active, match: { ...r.match }, target: { ...r.target } })}>Edit</Button>
          </span>
        </li>)}
        <li className="grid grid-cols-[32px_minmax(0,1fr)_auto] items-center gap-3 border-t border-line bg-surface-0 px-3 py-3">
          <span className="grid h-7 w-7 place-items-center rounded-full bg-surface-2 text-ink-3"><Icon name="arrow" size={14} /></span>
          <span className="text-small text-ink-2">No rule matched → default team <b className="text-ink">{team(c.defaultTeamId)?.name}</b>, round-robin among its active reps</span>
          <SelectField label="Default team" value={c.defaultTeamId} onChange={(e) => { const v = e.target.value; ask({ title: `Make ${team(v)?.name} the default team?`, done: 'Default team changed', run: (x) => api.setDefaultTeam(v, x) }); }} options={(teams.data ?? []).map((t) => ({ value: t.id, label: t.name }))} className="w-44" />
        </li>
      </ol>
    </section>

    <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
      <section className="flex min-w-0 flex-col gap-3" aria-labelledby="caps-h">
        <div><h2 id="caps-h" className="m-0 text-heading">Lead caps</h2><p className="m-0 text-small text-ink-2">Active leads a rep can hold. Round-robin skips reps at their cap; raising a cap places waiting leads straight away.</p></div>
        <div className="overflow-x-auto rounded-lg border border-line bg-surface-1"><table className="w-full border-collapse text-sm">
          <thead><tr>{['Rep', 'Team', 'Now', 'Cap', ''].map((h) => <th key={h} className="whitespace-nowrap border-b border-line bg-surface-2 px-3 py-2.5 text-left text-xs font-semibold text-ink-2">{h}</th>)}</tr></thead>
          <tbody>{c.caps.map((r) => { const t = team(r.teamId); const v = caps[r.userId] ?? String(r.cap); const changed = Number(v) !== r.cap; return <tr key={r.userId} className={r.active ? '' : 'opacity-60'}>
            <td className="border-b border-line px-3 py-2 font-semibold">{r.name}{!r.active && <span className="ml-2"><Badge>Inactive</Badge></span>}</td>
            <td className="border-b border-line px-3">{t && <TeamChip name={t.name} color={t.color} />}</td>
            <td className="w-[200px] border-b border-line px-3"><CapMeter active={r.activeLeads} cap={r.cap} /></td>
            <td className="border-b border-line px-3"><input type="number" min={0} max={500} aria-label={`Cap for ${r.name}`} value={v} onChange={(e) => setCaps((x) => ({ ...x, [r.userId]: e.target.value }))} className="h-8 w-20 rounded-md border border-line-control bg-surface-1 px-2 text-sm" /></td>
            <td className="border-b border-line px-3 text-right">{changed && <Button size="sm" variant="primary" onClick={() => ask({ title: `Change ${r.name}’s cap ${r.cap} → ${v}?`, done: 'Cap saved', run: async (x) => { const res = await api.setCap(r.userId, Number(v), x); setCaps((c0) => { const n = { ...c0 }; delete n[r.userId]; return n; }); return res; } })}>Save</Button>}</td>
          </tr>; })}</tbody>
        </table></div>
        <div className="panel flex flex-col gap-2 p-4"><h3 className="m-0 text-subheading">Rep groups</h3>{c.groups.map((g) => <p key={g.id} className="m-0 flex flex-wrap items-center gap-2 text-small"><b>{g.name}</b>{team(g.teamId) && <TeamChip name={team(g.teamId)!.name} color={team(g.teamId)!.color} />}<span className="text-ink-2">{g.memberIds.map((id) => users.data?.find((u) => u.id === id)?.name).join(', ')}</span></p>)}</div>
      </section>
      <TestRule />
    </div>
    {editing && <RuleDialog cfg={c} rule={editing} teams={(teams.data ?? []).map((t) => ({ value: t.id, label: t.name }))} reps={(users.data ?? []).filter((u) => u.role === 'user' && u.active).map((u) => ({ value: u.id, label: u.name }))} onClose={() => setEditing(null)} onSave={(r) => { setEditing(null); ask({ title: r.id ? `Save changes to “${r.name}”?` : `Create “${r.name}”?`, done: r.id ? 'Rule saved' : 'Rule created', run: (x) => api.saveRule(r, x) }); }} />}
    {element}
  </div>;
}

function RuleDialog({ cfg, rule, teams, reps, onClose, onSave }: { cfg: AssignmentConfig; rule: RuleInput; teams: { value: string; label: string }[]; reps: { value: string; label: string }[]; onClose: () => void; onSave: (r: RuleInput) => void }) {
  const [r, setR] = useState<RuleInput>(rule);
  const kind = r.target.groupId ? 'group' : r.target.userId ? 'rep' : 'team';
  const setKind = (k: string) => setR({ ...r, target: k === 'group' ? { groupId: cfg.groups[0]?.id } : k === 'rep' ? { userId: reps[0]?.value } : { teamId: teams[0]?.value } });
  const ok = r.name.trim() && (r.match.source || r.match.industry) && (r.target.teamId || r.target.groupId || r.target.userId);
  return <Dialog title={rule.id ? 'Edit rule' : 'New rule'} description="All conditions you set must match. Leave a condition empty to ignore it." onClose={onClose} wide footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!ok} onClick={() => onSave(r)}>Continue</Button></>}>
    <TextField label="Rule name" required value={r.name} onChange={(e) => setR({ ...r, name: e.target.value })} placeholder="e.g. Referral · Food & beverage → North F&B group" />
    <div className="grid gap-3 sm:grid-cols-2">
      <SelectField label="Lead source" value={r.match.source ?? ''} onChange={(e) => setR({ ...r, match: { ...r.match, source: (e.target.value || undefined) as RuleInput['match']['source'] } })} options={[{ value: '', label: 'Any source' }, ...LEAD_SOURCES.map((s) => ({ value: s, label: s }))]} />
      <SelectField label="Industry" value={r.match.industry ?? ''} onChange={(e) => setR({ ...r, match: { ...r.match, industry: e.target.value || undefined } })} options={[{ value: '', label: 'Any industry' }, ...INDUSTRIES.map((s) => ({ value: s, label: s }))]} />
    </div>
    <div className="grid gap-3 sm:grid-cols-2">
      <SelectField label="Send to" value={kind} onChange={(e) => setKind(e.target.value)} options={[{ value: 'team', label: 'A team' }, { value: 'group', label: 'A rep group' }, { value: 'rep', label: 'One rep' }]} />
      {kind === 'team' && <SelectField label="Team" value={r.target.teamId ?? ''} onChange={(e) => setR({ ...r, target: { teamId: e.target.value } })} options={teams} />}
      {kind === 'group' && <SelectField label="Group" value={r.target.groupId ?? ''} onChange={(e) => setR({ ...r, target: { groupId: e.target.value } })} options={cfg.groups.map((g) => ({ value: g.id, label: g.name }))} />}
      {kind === 'rep' && <SelectField label="Rep" value={r.target.userId ?? ''} onChange={(e) => setR({ ...r, target: { userId: e.target.value } })} options={reps} hint="Their cap still applies; at cap the lead waits." />}
    </div>
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={r.active} onChange={(e) => setR({ ...r, active: e.target.checked })} className="accent-[var(--brand)]" />Rule is on</label>
  </Dialog>;
}

function TestRule() {
  const [source, setSource] = useState('Referral'); const [industry, setIndustry] = useState('Food & beverage');
  const t = useMutation({ mutationFn: () => api.testRule({ source, industry: industry || undefined }) });
  return <section className="panel flex flex-col gap-3 p-5" aria-labelledby="test-h">
    <div><h2 id="test-h" className="m-0 text-heading">Test a rule</h2><p className="m-0 text-small text-ink-2">See where a lead would go. Nothing is saved or logged.</p></div>
    <SelectField label="Lead source" value={source} onChange={(e) => setSource(e.target.value)} options={LEAD_SOURCES.map((s) => ({ value: s, label: s }))} />
    <SelectField label="Industry" value={industry} onChange={(e) => setIndustry(e.target.value)} options={[{ value: '', label: 'Not set' }, ...INDUSTRIES.map((s) => ({ value: s, label: s }))]} />
    <Button icon="search" loading={t.isPending} onClick={() => t.mutate()}>Test</Button>
    {t.data && <Banner tone={t.data.wouldGoTo ? 'success' : 'warning'} title={t.data.wouldGoTo ? `Goes to ${t.data.wouldGoTo}` : 'It would wait in the Pool'}>{t.data.ruleName} · target {t.data.target}{t.data.waiting ? ` · ${WAITING_LABEL[t.data.waiting as keyof typeof WAITING_LABEL]}` : ''}</Banner>}
  </section>;
}

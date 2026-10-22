import clsx from 'clsx';
import { useEffect, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '@/auth/AuthProvider';
import { navFor, type NavItem } from '@/auth/navigation';
import { ROLE_LABEL } from '@/auth/permissions';
import { api } from '@/api';
import { BRAND } from '@/config/brand';
import { ENV } from '@/config/env';
import { Icon } from '@/components/ui/Icon';
import { Avatar } from '@/components/ui/Badge';
import { LogActivityProvider } from '@/features/deals/LogActivityDrawer';

/** Sidebar + top bar. The sidebar lists only what the person's role can open in the current release phase. */
export function AppShell() {
  const { me } = useAuth();
  const [open, setOpen] = useState(false);
  const notes = useQuery({ queryKey: ['notifications'], queryFn: api.notifications });
  const approvals = useQuery({ queryKey: ['approvals'], queryFn: api.approvals });
  const deals = useQuery({ queryKey: ['deals'], queryFn: api.deals, enabled: ENV.releasePhase >= 3 && !!me });
  if (!me) return null;
  const unread = notes.data?.filter((n) => !n.readAt).length ?? 0;
  const pending = approvals.data?.filter((a) => a.status === 'pending' && (me.role === 'admin' || me.role === 'super_admin')).length ?? 0;
  const counts: Record<string, { n: number; accent?: boolean }> = { notifications: { n: unread }, approvals: { n: pending, accent: true } };
  (deals.data ?? []).forEach((d) => {
    const won = d.status === 'won'; if (d.status === 'lost' || (won && d.stage !== 'onboarding' && d.stage !== 'updated')) return;
    const key = `stage:${d.pipeline}:${d.stage === 'revision_2' ? 'revision' : d.stage}`; (counts[key] ||= { n: 0 }).n++;
  });
  const items = navFor(me.role);
  const groups = items.reduce<Record<string, NavItem[]>>((acc, i) => { (acc[i.group ?? ''] ||= []).push(i); return acc; }, {});

  return (
    <LogActivityProvider>
    <div className="flex min-h-full">
      <nav aria-label="Main" className={clsx('fixed inset-y-0 left-0 z-40 flex w-sidebar shrink-0 flex-col gap-0.5 overflow-y-auto border-r border-line bg-surface-1 px-2 py-3 transition-transform lg:sticky lg:top-0 lg:h-screen lg:translate-x-0', open ? 'translate-x-0 shadow-pop' : '-translate-x-full')}>
        <div className="flex items-center gap-2 px-2.5 pb-4 pt-1.5">
          <img src={BRAND.logo.mark} alt="" className="h-7 w-auto" />
          <img src={BRAND.logo.wordmark} alt={BRAND.name} className="h-4 w-auto dark:invert" />
        </div>
        {Object.entries(groups).map(([g, list]) => (
          <NavGroup key={g || 'main'} label={g} list={list} counts={counts} onNavigate={() => setOpen(false)} />
        ))}
        <div className="mt-auto px-2.5 pt-4 text-[11px] leading-4 text-ink-3">Release phase {ENV.releasePhase} · {BRAND.tagline}</div>
      </nav>
      {open && <button aria-label="Close menu" className="fixed inset-0 z-30 bg-[var(--overlay)] lg:hidden" onClick={() => setOpen(false)} />}

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar unread={unread} onMenu={() => setOpen(true)} />
        <main className={clsx('mx-auto w-full max-w-[1440px] flex-1 px-4 pt-7 sm:px-8', ENV.releasePhase >= 2 ? 'pb-28' : 'pb-12')}><Outlet /></main>
      </div>
      {ENV.releasePhase >= 2 && <AddLeadFab />}
    </div>
    </LogActivityProvider>
  );
}

/**
 * A sidebar group. Groups of stage pages are dropdowns: closed by default to keep the sidebar calm,
 * opened by clicking the header, and opened automatically when you are on one of their pages.
 * The header shows the group's total deal count while closed. Open/closed is remembered per browser.
 */
function NavGroup({ label, list, counts, onNavigate }: { label: string; list: NavItem[]; counts: Record<string, { n: number; accent?: boolean }>; onNavigate: () => void }) {
  const { pathname } = useLocation();
  const collapsible = !!label && list.some((i) => i.stage);
  const here = list.some((i) => pathname.startsWith(i.path) && i.path !== '/app');
  const key = 'relay.nav.' + label;
  const [open, setOpenState] = useState<boolean>(() => { try { return localStorage.getItem(key) === '1'; } catch { return false; } });
  const toggle = () => setOpenState((o) => { try { localStorage.setItem(key, o ? '0' : '1'); } catch { /* ignore */ } return !o; });
  const shown = !collapsible || open || here;
  const total = list.reduce((n, i) => n + (counts[i.id]?.n ?? 0), 0);
  const id = 'nav-' + label.toLowerCase().replace(/\W+/g, '-');
  return <div className="flex flex-col gap-0.5">
    {label && (collapsible
      ? <button type="button" onClick={toggle} aria-expanded={shown} aria-controls={id}
          className={clsx('mt-2 flex h-[34px] items-center gap-2 rounded-md px-2.5 text-[13px] font-semibold hover:bg-surface-3', here ? 'text-brand-ink' : 'text-ink-2 hover:text-ink')}>
          <Icon name="chevron" size={14} className={clsx('shrink-0 transition-transform duration-150', shown ? 'rotate-90' : '')} />
          <span className="min-w-0 flex-1 truncate whitespace-nowrap text-left">{label}</span>
          {!shown && total > 0 && <span className="min-w-5 rounded-pill bg-surface-2 px-1.5 text-center text-[11px] font-semibold normal-case leading-[18px] tracking-normal text-ink-2">{total}</span>}
        </button>
      : <div className="px-2.5 pb-1 pt-3.5 text-overline text-ink-3">{label}</div>)}
    {shown && <div id={id} className="flex flex-col gap-0.5">{list.map((i) => (
      <NavLink key={i.id} to={i.path} end={i.path === '/app'} onClick={onNavigate}
        className={({ isActive }) => clsx('flex items-center gap-2.5 rounded-md px-2.5 text-sm font-medium no-underline', i.stage ? 'h-[30px] pl-6 text-[13px]' : 'h-[34px]', isActive ? 'bg-brand-soft font-semibold text-brand-ink' : 'text-ink-2 hover:bg-surface-3 hover:text-ink')}>
        {i.stage ? <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-current opacity-60" /> : <Icon name={i.icon} size={18} />}<span className="min-w-0 truncate whitespace-nowrap">{i.label}</span>
        {counts[i.id]?.n ? <span className={clsx('ml-auto min-w-5 rounded-pill px-1.5 text-center text-[11px] font-semibold leading-[18px]', counts[i.id].accent ? 'bg-accent text-accent-on' : 'bg-surface-2 text-ink-2')}>{counts[i.id].n}</span> : null}
      </NavLink>
    ))}</div>}
  </div>;
}

/** "Add new lead" everywhere except the pages that already carry it as their main button. */
function AddLeadFab() {
  const { pathname } = useLocation(); const nav = useNavigate();
  if (['/app', '/app/leads/new', '/app/pool', '/app/board'].includes(pathname.replace(/\/$/, ''))) return null;
  return <button onClick={() => nav('/app/leads/new')} aria-label="Add new lead" title="Add new lead"
    className="group fixed bottom-6 right-6 z-30 flex h-14 items-center gap-2 rounded-pill bg-brand pl-4 pr-4 text-brand-on shadow-pop transition-all hover:bg-brand-hover hover:pr-5">
    <Icon name="plus" size={22} /><span className="max-w-0 overflow-hidden whitespace-nowrap text-sm font-semibold transition-all group-hover:max-w-[120px] group-focus-visible:max-w-[120px]">Add new lead</span>
  </button>;
}

function TopBar({ unread, onMenu }: { unread: number; onMenu: () => void }) {
  const { me, signOut } = useAuth();
  const nav = useNavigate();
  const [menu, setMenu] = useState(false);
  const [dark, setDark] = useState(() => {
    // Relay is light white and blue by default; dark mode only when the person picks it from the menu.
    try { return localStorage.getItem('relay.theme') === 'dark'; } catch { return false; }
  });
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { document.documentElement.dataset.theme = dark ? 'dark' : 'light'; }, [dark]);
  const toggleTheme = () => setDark((d) => { try { localStorage.setItem('relay.theme', d ? 'light' : 'dark'); } catch { /* ignore */ } return !d; });
  useEffect(() => { const f = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setMenu(false); }; document.addEventListener('mousedown', f); return () => document.removeEventListener('mousedown', f); }, []);
  if (!me) return null;
  return (
    <header className="sticky top-0 z-10 flex h-topbar items-center gap-3 border-b border-line bg-surface-1 px-4 sm:px-8">
      <button className="grid h-9 w-9 place-items-center rounded-md text-ink-2 hover:bg-surface-3 lg:hidden" aria-label="Open menu" onClick={onMenu}><Icon name="menu" size={20} /></button>
      <label className="flex h-9 max-w-[480px] flex-1 items-center gap-2 rounded-md border border-line-control bg-surface-0 px-2.5 text-ink-3">
        <Icon name="search" /><span className="sr-only">Search</span>
        <input type="search" placeholder="Search people, teams and requests" className="min-w-0 flex-1 border-0 bg-transparent text-sm text-ink outline-none" />
      </label>
      <div className="ml-auto flex items-center gap-2">
        <button onClick={() => nav('/app/notifications')} aria-label={`Notifications, ${unread} unread`} className="relative grid h-9 w-9 place-items-center rounded-md text-ink-2 hover:bg-surface-3">
          <Icon name="bell" size={20} />
          {unread > 0 && <span className="absolute right-0.5 top-1 min-w-4 rounded-pill bg-accent px-1 text-center text-[10px] font-semibold leading-4 text-accent-on">{unread}</span>}
        </button>
        <div className="relative" ref={ref}>
          <button onClick={() => setMenu((m) => !m)} aria-expanded={menu} aria-haspopup="menu" className="flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-surface-3">
            <Avatar name={me.name} size={32} />
            <span className="hidden flex-col text-left sm:flex"><span className="text-[13px] font-semibold leading-[18px]">{me.name}</span><span className="text-xs text-ink-3">{ROLE_LABEL[me.role]}{me.team ? ` · ${me.team.name}` : ' · All teams'}</span></span>
          </button>
          {menu && (
            <div role="menu" className="absolute right-0 top-12 z-50 w-56 rounded-lg border border-line bg-surface-1 p-1 shadow-pop">
              <MenuItem icon="user" onClick={() => { setMenu(false); nav('/app/profile'); }}>My profile</MenuItem>
              <MenuItem icon="bell" onClick={() => { setMenu(false); nav('/app/settings/notifications'); }}>Notification settings</MenuItem>
              <MenuItem icon="eye" onClick={toggleTheme}>{dark ? 'Light mode' : 'Dark mode'}</MenuItem>
              <div className="my-1 h-px bg-line" />
              <MenuItem icon="logout" onClick={async () => { await signOut(); nav('/login', { replace: true }); }}>Sign out</MenuItem>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

function MenuItem({ icon, children, onClick }: { icon: Parameters<typeof Icon>[0]['name']; children: string; onClick: () => void }) {
  return <button role="menuitem" onClick={onClick} className="flex h-9 w-full items-center gap-2.5 rounded-md px-2.5 text-left text-sm text-ink hover:bg-surface-3"><Icon name={icon} />{children}</button>;
}

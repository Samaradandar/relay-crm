/* Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta. */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import type { Account } from '@/api/types';
import { PageHeader } from '@/components/layout/PageHeader';
import { Column, DataTable } from '@/components/ui/DataTable';
import { TextField } from '@/components/ui/Field';

export function AccountsListPage() {
  const [q, setQ] = useState('');
  const query = useQuery({ queryKey: ['accounts', q], queryFn: () => api.accounts(q || undefined) });
  const rows = query.data ?? [];

  const columns: Column<Account>[] = [
    { key: 'name', label: 'Account', render: (a) => <Link to={`/app/accounts/${a.id}`} className="font-semibold text-brand-ink no-underline hover:underline">{a.name}</Link> },
    { key: 'industry', label: 'Industry', render: (a) => a.industry ?? '—' },
    { key: 'city', label: 'City', render: (a) => a.city ?? '—' },
    { key: 'website', label: 'Website', render: (a) => a.website ?? '—' }
  ];

  return <div className="flex flex-col gap-5">
    <PageHeader title="Accounts" subtitle="Every company Relay tracks, lead or customer." />
    <TextField label="Search" optionalNote="(name, website)" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search accounts…" className="max-w-sm" />
    <DataTable columns={columns} rows={rows} empty={query.isLoading ? 'Loading…' : 'No accounts found.'} />
  </div>;
}

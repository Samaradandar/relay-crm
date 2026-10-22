import type { ApprovalRequest, FailedJob, HistoryEntry, Notification, NotificationPref, Team, User } from '../types';

/** Sample data for mock mode. Matches the seed in api/scripts/seed_dev.py (one person per role). */
const h = (hoursAgo: number) => new Date(Date.now() - hoursAgo * 3_600_000).toISOString();

export const MOCK_PASSWORD = 'relay123';

export const db = {
  teams: [
    { id: 't-north', name: 'Team North', color: '#2E9E8F', teamLeaderId: 'u-rohit', createdAt: '2026-09-02T04:30:00Z', memberIds: ['u-rohit', 'u-priya', 'u-ankit', 'u-divya'] },
    { id: 't-south', name: 'Team South', color: '#D9704A', teamLeaderId: 'u-neha', createdAt: '2026-09-02T04:30:00Z', memberIds: ['u-neha', 'u-rahul', 'u-kavya'] },
    { id: 't-ent', name: 'Enterprise', color: '#6C63C7', teamLeaderId: 'u-sanjay', createdAt: '2026-09-15T04:30:00Z', memberIds: ['u-sanjay', 'u-sneha'] }
  ] as Team[],
  users: [
    { id: 'u-vikram', name: 'Vikram Singh', email: 'vikram@relay.ai', role: 'super_admin', teamId: null, active: true, leadCap: null, lastSignInAt: h(5), createdAt: '2026-09-01T04:30:00Z' },
    { id: 'u-arjun', name: 'Arjun Rao', email: 'arjun.rao@relay.ai', role: 'admin', teamId: null, active: true, leadCap: null, lastSignInAt: h(6), createdAt: '2026-09-01T05:00:00Z' },
    { id: 'u-neha', name: 'Neha Kapoor', email: 'neha.kapoor@relay.ai', role: 'team_leader', teamId: 't-south', active: true, leadCap: 10, lastSignInAt: h(4), createdAt: '2026-09-02T05:00:00Z' },
    { id: 'u-rohit', name: 'Rohit Bansal', email: 'rohit.bansal@relay.ai', role: 'team_leader', teamId: 't-north', active: true, leadCap: 10, lastSignInAt: h(20), createdAt: '2026-09-02T05:00:00Z' },
    { id: 'u-sanjay', name: 'Sanjay Kulkarni', email: 'sanjay.k@relay.ai', role: 'team_leader', teamId: 't-ent', active: true, leadCap: 8, lastSignInAt: h(30), createdAt: '2026-09-15T05:00:00Z' },
    { id: 'u-priya', name: 'Priya Sharma', email: 'priya.sharma@relay.ai', role: 'user', teamId: 't-north', active: true, leadCap: 25, lastSignInAt: h(5), createdAt: '2026-09-02T05:05:00Z' },
    { id: 'u-rahul', name: 'Rahul Mehta', email: 'rahul.mehta@relay.ai', role: 'user', teamId: 't-south', active: true, leadCap: 25, lastSignInAt: h(4), createdAt: '2026-09-02T05:05:00Z' },
    { id: 'u-ankit', name: 'Ankit Verma', email: 'ankit.verma@relay.ai', role: 'user', teamId: 't-north', active: true, leadCap: 20, lastSignInAt: h(90), createdAt: '2026-09-02T05:05:00Z' },
    { id: 'u-divya', name: 'Divya Nair', email: 'divya.nair@relay.ai', role: 'user', teamId: 't-north', active: true, leadCap: 20, lastSignInAt: h(26), createdAt: '2026-09-03T05:05:00Z' },
    { id: 'u-kavya', name: 'Kavya Reddy', email: 'kavya.reddy@relay.ai', role: 'user', teamId: 't-south', active: true, leadCap: 20, lastSignInAt: h(28), createdAt: '2026-09-03T05:05:00Z' },
    { id: 'u-sneha', name: 'Sneha Pillai', email: 'sneha.pillai@relay.ai', role: 'user', teamId: 't-ent', active: false, leadCap: 20, lastSignInAt: h(600), createdAt: '2026-09-16T05:05:00Z' }
  ] as User[],
  approvals: [
    { id: 'a1', type: 'user.create', title: 'Add Karan Joshi as a sales rep', summary: 'Sales rep · Team South · karan.joshi@relay.ai', effect: 'Karan gets an account with the User role in Team South and can sign in straight away.', requestedBy: 'u-neha', requestedByName: 'Neha Kapoor', requestedAt: h(46), reason: 'Karan joins on 8 Oct to cover Pune accounts. Offer letter signed.', status: 'pending', changes: [{ label: 'Name', from: null, to: 'Karan Joshi' }, { label: 'Email', from: null, to: 'karan.joshi@relay.ai' }, { label: 'Role', from: null, to: 'User (sales rep)' }, { label: 'Team', from: null, to: 'Team South' }] },
    { id: 'a2', type: 'user.change_role', title: 'Make Divya Nair a Team Leader', summary: 'Sales rep → Team Leader · new team SMB West', effect: 'Divya becomes Team Leader of SMB West. Her open records stay hers.', requestedBy: 'u-rohit', requestedByName: 'Rohit Bansal', requestedAt: h(26), reason: 'SMB West is being split from Team North; Divya has run the SMB accounts since June.', status: 'pending', changes: [{ label: 'Role', from: 'User (sales rep)', to: 'Team Leader' }, { label: 'Team', from: 'Team North', to: 'SMB West (new)' }] },
    { id: 'a3', type: 'user.create', title: 'Add Farhan Ali as a sales rep', summary: 'Sales rep · Team North · farhan.ali@relay.ai', effect: 'Farhan gets an account with the User role in Team North.', requestedBy: 'u-rohit', requestedByName: 'Rohit Bansal', requestedAt: h(3), reason: 'Replacement for Ankit, who leaves on 15 Oct.', status: 'pending', changes: [{ label: 'Name', from: null, to: 'Farhan Ali' }, { label: 'Email', from: null, to: 'farhan.ali@relay.ai' }, { label: 'Role', from: null, to: 'User (sales rep)' }, { label: 'Team', from: null, to: 'Team North' }] },
    { id: 'a4', type: 'deal.team_transfer', teamIds: ['t-north', 't-south'], title: 'Team North asks to take ACME Foods from Team South', summary: 'Take · for Priya Sharma · from Team South', effect: 'ACME Foods moves to Team North and Priya Sharma. Team South loses access straight away; full history moves with the deal.', requestedBy: 'u-rohit', requestedByName: 'Rohit Bansal', requestedAt: h(5), reason: 'Priya already runs ACME’s sister brand (ACME Bakes) and has the owner’s number.', status: 'pending', changes: [{ label: 'Team', from: 'Team South', to: 'Team North' }, { label: 'Owner', from: 'Rahul Mehta', to: 'Priya Sharma' }] },
    { id: 'a5', type: 'deal.team_transfer', teamIds: ['t-south', 't-ent'], title: 'Team South gives Orion Malls to Enterprise', summary: 'Give · to Enterprise · Admin to name the rep', effect: 'Orion Malls moves to Enterprise. Its Team Leader or the Admin picks the rep.', requestedBy: 'u-neha', requestedByName: 'Neha Kapoor', requestedAt: h(28), reason: '14 outlets across 3 cities; needs the enterprise playbook.', status: 'pending', changes: [{ label: 'Team', from: 'Team South', to: 'Enterprise' }] }
  ] as ApprovalRequest[],
  notifications: [
    { id: 'n1', audience: 'admins', type: 'approval', title: 'New approval request from Rohit Bansal', body: 'Add Farhan Ali as a sales rep in Team North.', createdAt: h(3), readAt: null, link: '/app/approvals' },
    { id: 'n2', audience: 'admins', type: 'approval', title: 'Reminder: 3 requests are waiting for you', body: 'Oldest: Add Karan Joshi (raised 2 days ago).', createdAt: h(3.2), readAt: null, link: '/app/approvals' },
    { id: 'n3', audience: 'admins', type: 'system', title: '2 background jobs failed', body: 'Welcome emails to 2 new users could not be sent. Open Failed jobs to retry.', createdAt: h(4.5), readAt: null, link: '/app/admin/jobs' },
    { id: 'n4', audience: 'admins', type: 'approval', title: 'Vikram Singh approved a request', body: 'Lead cap for Priya Sharma changed from 20 to 25.', createdAt: h(20), readAt: h(19) },
    { id: 'n6', audience: 'everyone', type: 'people', title: 'Welcome to Relay CRM', body: 'Your workspace is ready. More pages switch on with each release.', createdAt: h(2), readAt: null },
    { id: 'n7', userId: 'u-priya', type: 'lead', title: 'New lead: Sharma Sweets & Bakers', body: 'Assigned to you by round-robin. Next step due today.', createdAt: h(0.6), readAt: null, link: '/app' },
    { id: 'n8', userId: 'u-priya', type: 'lead', title: 'Rohit Bansal assigned you Apex Diagnostics', body: 'Manual assignment from the Lead Pool.', createdAt: h(2.2), readAt: null, link: '/app' },
    { id: 'n9', userId: 'u-rohit', type: 'lead', title: 'Chai Point Express is waiting in the Pool', body: 'Every rep in North F&B group is at their cap. Assign it by hand or raise a cap.', createdAt: h(1.5), readAt: null, link: '/app' },
    { id: 'n10', userId: 'u-sanjay', type: 'lead', title: 'Orbit Events Pvt Ltd is waiting in the Pool', body: 'Enterprise has no active sales rep.', createdAt: h(9), readAt: null, link: '/app' },
    { id: 'n5', audience: 'everyone', type: 'people', title: 'Divya Nair moved to Team North', body: 'By Vikram Singh. Reason: SMB accounts consolidated.', createdAt: h(27), readAt: h(26) }
  ] as Notification[],
  prefs: [
    { type: 'approval.requested', label: 'Approval requested', description: 'Someone needs your decision on a request.', required: true, email: true },
    { type: 'approval.decided', label: 'Approval decided', description: 'A request you raised was approved or rejected.', required: true, email: true },
    { type: 'approval.reminder', label: 'Daily approval reminder', description: '09:30 IST, while requests are waiting for you.', required: false, email: true },
    { type: 'user.role_changed', label: 'Role or team changed', description: 'Your role or team was changed by an Admin.', required: true, email: true },
    { type: 'security.new_sign_in', label: 'New sign-in', description: 'Your account signed in from a new browser or place.', required: false, email: false }
  ] as NotificationPref[],
  failedJobs: [
    { id: 'j1', event: 'user.invited', title: 'Welcome email · Karan Joshi', causedBy: 'Arjun Rao', attempts: 10, lastError: 'Sending domain not verified (SES). Ask IT to verify it.', failedAt: h(4.6) },
    { id: 'j2', event: 'user.invited', title: 'Welcome email · Farhan Ali', causedBy: 'Vikram Singh', attempts: 10, lastError: 'Sending domain not verified (SES). Ask IT to verify it.', failedAt: h(4.5) },
    { id: 'j3', event: 'user.team_changed', title: 'Live update · team change', causedBy: 'Vikram Singh', attempts: 10, lastError: 'Socket listener restarted during delivery.', failedAt: h(27) }
  ] as FailedJob[],
  history: [
    { id: 'hx1', kind: 'assign', title: 'Signed in', actor: 'You', at: h(5) },
    { id: 'hx2', kind: 'alert', title: 'Failed sign-in (wrong password)', actor: 'Chrome, Bengaluru', at: h(52) },
    { id: 'hx3', kind: 'note', title: 'Lead cap changed 20 → 25', actor: 'Arjun Rao (Admin)', at: h(75), reason: 'Covering Ankit’s territory' },
    { id: 'hx4', kind: 'stage', title: 'Added to Team North', actor: 'Vikram Singh (Super admin)', at: '2026-09-02T04:35:00Z' }
  ] as HistoryEntry[]
};

import full from '@/assets/brand/logo-full.png';
import mark from '@/assets/brand/logo-mark.png';
import wordmark from '@/assets/brand/logo-wordmark.png';
import wordmarkWhite from '@/assets/brand/logo-wordmark-white.png';

/** Brand copy and logo files (cut from the supplied Relay CRM logo) used by the opening screen, sign-in and sidebar. */
export const BRAND = {
  name: 'Relay CRM',
  tagline: 'Designed for Adandar Technology',
  promise: 'Every lead, every move, on the record.',
  logo: { full, mark, wordmark, wordmarkWhite }
} as const;

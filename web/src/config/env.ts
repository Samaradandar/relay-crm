const e = import.meta.env;
export const ENV = {
  apiMode: (e.VITE_API_MODE ?? 'mock') as 'mock' | 'live',
  releasePhase: Number(e.VITE_RELEASE_PHASE ?? 1),
  router: (e.VITE_ROUTER ?? 'browser') as 'browser' | 'memory',
  cognito: {
    domain: (e.VITE_COGNITO_DOMAIN ?? '') as string,
    clientId: (e.VITE_COGNITO_CLIENT_ID ?? '') as string,
    redirectUri: (e.VITE_COGNITO_REDIRECT_URI ?? '') as string
  }
};
